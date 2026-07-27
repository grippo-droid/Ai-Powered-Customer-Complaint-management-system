"""
The Groq client wrapper - the only place in the project that talks to an LLM.

Exposes exactly one function, call_structured(), which takes a Pydantic model
and returns a validated instance of it. Every node in the graph goes through
it, so retry behaviour, JSON repair and error handling are written once.

The contract:

    call_structured(system, user, ComplaintFields)  ->  ComplaintFields

    ...or raises LLMError, whose message is safe to show in the chat.

There is no raw string parsing anywhere. The model's reply is JSON-decoded and
then validated by the same Pydantic model whose schema was injected into the
prompt - so a hallucinated field name, a wrong type, or a missing required key
is a ValidationError we can act on, not silent bad data on a QA record.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Type, TypeVar

from groq import APIConnectionError, APIStatusError, BadRequestError, Groq
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMError(Exception):
    """A failure whose message is safe and useful to show directly in the chat."""


# The API key lives here and only here. It is read from settings (server-side
# env), never sent to the browser, and never included in any API response.
_client = Groq(api_key=settings.groq_api_key, timeout=45.0, max_retries=2)

# Groq supports response_format={"type": "json_object"} on some models but not
# all, and support varies over time. Rather than hard-code an assumption about
# gemma2-9b-it, we try it once and remember the answer for the process.
#   None  = not tried yet, True = works, False = model rejected it
_json_mode_supported: bool | None = None


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def _extract_json_object(raw: str) -> str:
    """
    Pull the first complete JSON object out of a model reply.

    Small models ignore "no code fences" instructions often enough that this
    is not optional. Handles:
        ```json { ... } ```        fenced output
        Here is the JSON: { ... }  chatty preamble
        { ... } Hope this helps!   chatty suffix

    Scans for a brace-balanced span rather than using a regex, so nested
    objects and braces inside string literals are handled correctly.
    """
    text = raw.strip()

    if text.startswith("```"):
        # Drop the opening fence (```json or ```) and anything after the closer.
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.lstrip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()

    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in the model's reply")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("the model's JSON object was truncated before it closed")


# ---------------------------------------------------------------------------
# The Groq call
# ---------------------------------------------------------------------------


def _chat(messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
    """One Groq round trip. Translates SDK errors into user-facing LLMError."""
    global _json_mode_supported

    kwargs: Dict[str, Any] = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if _json_mode_supported is not False:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = _client.chat.completions.create(**kwargs)
    except BadRequestError as exc:
        # Most likely the model does not accept response_format. Remember that
        # and retry once without it; the prompt already demands bare JSON, so
        # this degrades cleanly rather than failing the request.
        if _json_mode_supported is not False and "response_format" in str(exc).lower():
            logger.warning("Model %s rejected JSON mode; falling back.", settings.groq_model)
            _json_mode_supported = False
            kwargs.pop("response_format", None)
            response = _client.chat.completions.create(**kwargs)
        else:
            raise LLMError(f"The AI service rejected the request: {exc}") from exc
    except APIConnectionError as exc:
        raise LLMError(
            "I couldn't reach the AI service. Please check the network connection "
            "and try again."
        ) from exc
    except APIStatusError as exc:
        if exc.status_code == 401:
            raise LLMError(
                "The AI service rejected the API key. Please check GROQ_API_KEY in .env."
            ) from exc
        if exc.status_code == 429:
            raise LLMError(
                "The AI service is rate-limited right now. Please wait a moment and try again."
            ) from exc
        raise LLMError(f"The AI service returned an error ({exc.status_code}).") from exc

    if _json_mode_supported is None:
        _json_mode_supported = True

    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise LLMError("The AI returned an empty response.")
    return content


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def call_structured(
    system_prompt: str,
    user_prompt: str,
    output_model: Type[ModelT],
    *,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> ModelT:
    """
    Call the LLM and return a validated instance of `output_model`.

    Error strategy, in order:

      1. Call the model, extract the JSON object, validate through Pydantic.
      2. On malformed JSON or a ValidationError, retry ONCE - feeding the
         model its own bad output plus the exact error text. Small models
         correct themselves well when shown the specific problem, and this
         is far cheaper than failing the user's turn.
      3. If the retry also fails, raise LLMError. The graph turns that into
         an assistant chat message and leaves the form state untouched.

    Never returns partially valid data. Either the caller gets a fully
    validated model, or an exception - a half-filled QA record is worse
    than a visible error.

    temperature defaults to 0.0: extraction and classification should be
    deterministic and repeatable, which also makes the demo reproducible.
    """
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error = ""
    raw = ""

    for attempt in (1, 2):
        raw = _chat(messages, temperature=temperature, max_tokens=max_tokens)

        try:
            payload = _extract_json_object(raw)
            return output_model.model_validate_json(payload)

        except (ValueError, json.JSONDecodeError) as exc:
            last_error = f"The reply was not valid JSON: {exc}"

        except ValidationError as exc:
            # errors() is far more targeted than str(exc) - it names the exact
            # field and the exact problem, which is what the model needs.
            problems = "; ".join(
                f"field '{'.'.join(str(p) for p in err['loc'])}': {err['msg']}"
                for err in exc.errors()[:5]
            )
            last_error = f"The JSON did not match the required schema: {problems}"

        if attempt == 1:
            logger.warning(
                "Structured output failed for %s (attempt 1): %s",
                output_model.__name__,
                last_error,
            )
            # Show the model its own output and the specific fault, then ask
            # again. Appending to `messages` keeps it as a real conversation
            # rather than a blind re-roll of the same prompt.
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That response was rejected. {last_error}\n\n"
                        "Return ONLY a corrected JSON object matching the schema "
                        "exactly. No markdown, no code fences, no explanation."
                    ),
                }
            )

    logger.error(
        "Structured output failed twice for %s. Last error: %s. Raw reply: %.300s",
        output_model.__name__,
        last_error,
        raw,
    )
    raise LLMError(
        "The AI returned a response I couldn't read, twice in a row. "
        "Your form has been left unchanged - please try rephrasing, or enter "
        "the details manually."
    )

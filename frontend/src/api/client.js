/**
 * The only module that talks to the backend.
 *
 * Four calls, matching the four endpoints. Nothing here knows about Redux,
 * and nothing else in the app knows about fetch or URLs.
 *
 * There is no API key in this file, or anywhere in the frontend. The browser
 * talks to our FastAPI backend; only the backend talks to Groq.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * Unwrap a response, turning a FastAPI error into a readable Error.
 *
 * FastAPI returns {"detail": "..."} for HTTPException, but {"detail": [...]}
 * for a 422 validation error, so both shapes are handled.
 */
async function unwrap(response) {
  if (response.ok) return response.json();

  let message = `Request failed (${response.status})`;
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') {
      message = body.detail;
    } else if (Array.isArray(body.detail) && body.detail.length) {
      message = body.detail.map((d) => d.msg).join('; ');
    }
  } catch {
    // Body was not JSON - keep the status-code message.
  }
  throw new Error(message);
}

/** POST /complaints/session - resumes `existingId` if it is still open. */
export async function startSession(existingId = null) {
  const response = await fetch(`${BASE_URL}/complaints/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: existingId }),
  });
  return unwrap(response);
}

/** GET /complaints/session/{id} - rehydrate the UI after a page refresh. */
export async function fetchSession(sessionId) {
  const response = await fetch(`${BASE_URL}/complaints/session/${sessionId}`);
  return unwrap(response);
}

/**
 * POST /complaints/session/{id}/message
 *
 * Always multipart/form-data, with whichever part is relevant. We must NOT
 * set Content-Type by hand here: the browser has to generate it so it can
 * include the multipart boundary.
 */
export async function sendMessage(sessionId, { text = '', file = null }) {
  const form = new FormData();
  if (text) form.append('message', text);
  if (file) form.append('file', file);

  const response = await fetch(`${BASE_URL}/complaints/session/${sessionId}/message`, {
    method: 'POST',
    body: form,
  });
  return unwrap(response);
}

/**
 * POST /complaints/session/{id}/commit
 *
 * Sends the form state the user is actually looking at, so any manual edits
 * they made take precedence over what the AI last produced.
 */
export async function commitSession(sessionId, formState) {
  const response = await fetch(`${BASE_URL}/complaints/session/${sessionId}/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ form_state: formState }),
  });
  return unwrap(response);
}

/** GET /complaints - the committed ledger. */
export async function listComplaints() {
  const response = await fetch(`${BASE_URL}/complaints`);
  return unwrap(response);
}

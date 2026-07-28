/**
 * The only module that talks to the backend.
 *
 * Nothing here knows about Redux, and nothing else in the app knows about
 * fetch, URLs, or where the auth token is kept.
 *
 * There is no API key in this file, or anywhere in the frontend. The browser
 * talks to our FastAPI backend; only the backend talks to Groq.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const TOKEN_STORAGE_KEY = 'qms.token';

/**
 * The JWT, held here because this is the module that attaches it.
 *
 * Mirrored into localStorage so a refresh does not log the user out. That is
 * a real trade-off and worth being honest about: localStorage is readable by
 * any script on the page, so a cross-site-scripting bug would expose the
 * token. The alternative - an httpOnly cookie the JavaScript cannot read -
 * is what a production system should use, but it needs CSRF protection and a
 * same-site story that a cross-origin demo (Vercel talking to Render) makes
 * considerably more involved. Stated here rather than glossed over.
 */
let authToken = localStorage.getItem(TOKEN_STORAGE_KEY);

/** Called by the auth slice on login and signup. */
export function setAuthToken(token) {
  authToken = token;
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

/** Called on logout, and whenever the server tells us the token is no good. */
export function clearAuthToken() {
  authToken = null;
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export function getAuthToken() {
  return authToken;
}

/**
 * What to do when the backend says 401.
 *
 * Set once by the store. A callback rather than an import of the store keeps
 * the dependency pointing one way - the store knows about this module, and
 * this module knows nothing about the store - which is what stops the two
 * from forming an import cycle.
 */
let onUnauthorized = () => {};

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

/** Bearer header, or nothing at all if we have no token. */
function authHeaders() {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

/** An Error that also carries the HTTP status, so callers can branch on it. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * Unwrap a response, turning a FastAPI error into a readable Error.
 *
 * FastAPI returns {"detail": "..."} for HTTPException, but {"detail": [...]}
 * for a 422 validation error, so both shapes are handled.
 *
 * A 401 means the token is missing, expired or invalid, and every subsequent
 * request would fail the same way - so the token is dropped and the app is
 * told to show the login page. A 403 is deliberately NOT treated this way:
 * the user is correctly identified and simply may not do that thing, so
 * logging them out would be both wrong and confusing.
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

  if (response.status === 401) {
    clearAuthToken();
    onUnauthorized();
  }

  throw new ApiError(message, response.status);
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/** POST /auth/signup - creates the account and logs straight in. */
export async function signup({ email, password, role }) {
  const response = await fetch(`${BASE_URL}/auth/signup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, role }),
  });
  return unwrap(response);
}

/** POST /auth/login */
export async function login({ email, password }) {
  const response = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  return unwrap(response);
}

/** GET /auth/me - is the stored token still good, and who is it for? */
export async function fetchCurrentUser() {
  const response = await fetch(`${BASE_URL}/auth/me`, { headers: authHeaders() });
  return unwrap(response);
}

// ---------------------------------------------------------------------------
// Complaints - every one of these requires a valid token
// ---------------------------------------------------------------------------

/** POST /complaints/session - resumes `existingId` if it is still open. */
export async function startSession(existingId = null) {
  const response = await fetch(`${BASE_URL}/complaints/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ session_id: existingId }),
  });
  return unwrap(response);
}

/** GET /complaints/session/{id} - rehydrate the UI after a page refresh. */
export async function fetchSession(sessionId) {
  const response = await fetch(`${BASE_URL}/complaints/session/${sessionId}`, {
    headers: authHeaders(),
  });
  return unwrap(response);
}

/**
 * POST /complaints/session/{id}/message
 *
 * Always multipart/form-data, with whichever part is relevant. We must NOT
 * set Content-Type by hand here: the browser has to generate it so it can
 * include the multipart boundary. Authorization is fine to set - it is only
 * Content-Type that the browser needs to own.
 */
export async function sendMessage(sessionId, { text = '', file = null }) {
  const form = new FormData();
  if (text) form.append('message', text);
  if (file) form.append('file', file);

  const response = await fetch(`${BASE_URL}/complaints/session/${sessionId}/message`, {
    method: 'POST',
    headers: authHeaders(),
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
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ form_state: formState }),
  });
  return unwrap(response);
}

/** GET /complaints - the committed ledger. */
export async function listComplaints() {
  const response = await fetch(`${BASE_URL}/complaints`, { headers: authHeaders() });
  return unwrap(response);
}

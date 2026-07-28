/**
 * The single Redux slice for the whole app.
 *
 * Both panels read from here: the chat renders `messages`, the form renders
 * `fields` and `riskAssessment`, and both are written by the same thunk. That
 * is the point of putting them in one store - an assistant turn updates the
 * chat and the form in one atomic state change, so they can never disagree.
 *
 * `changedFields` is the interesting one. The backend tells us which fields
 * it touched this turn; we hold that list in state, the form highlights those
 * fields, and a timer clears it. The UI never has to diff anything itself.
 */

import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import * as api from '../api/client';
import { logout, sessionExpired } from './authSlice';

const SESSION_STORAGE_KEY = 'qms.sessionId';

/**
 * How long to wait before telling the user the server is still starting.
 *
 * The hosted demo runs on a free tier that stops the container after a period
 * of inactivity, so the first request after a quiet spell has to wait for it
 * to boot - typically 30-60 seconds. Locally this never fires, because the
 * response arrives in milliseconds.
 */
const WAKE_NOTICE_MS = 4000;

/** Delay before the single boot retry. Cold starts can 502 at the proxy. */
const BOOT_RETRY_MS = 3000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Controlled React inputs need '' rather than null/undefined, so the empty
// form is defined here and the API's nulls are converted on the way in.
export const EMPTY_FIELDS = {
  complaint_source: '',
  customer_name: '',
  product_name: '',
  product_strength: '',
  batch_number: '',
  affected_quantity: '',
  manufacturing_date: '',
  expiry_date: '',
  complaint_type: '',
  complaint_date: '',
  complaint_description: '',
};

export const EMPTY_RISK = {
  complaint_summary: '',
  severity_suggested: '',
  suggested_next_action: '',
  initial_risk_assessment: '',
  root_cause_suggestion: '',
  capa_recommendation: '',
};

function normalizeFields(apiFields) {
  const result = { ...EMPTY_FIELDS };
  Object.keys(EMPTY_FIELDS).forEach((key) => {
    result[key] = apiFields?.[key] ?? '';
  });
  return result;
}

function normalizeRisk(apiRisk) {
  if (!apiRisk) return { ...EMPTY_RISK };
  return {
    complaint_summary: apiRisk.complaint_summary ?? '',
    severity_suggested: apiRisk.severity_suggested ?? '',
    suggested_next_action: apiRisk.suggested_next_action ?? '',
    initial_risk_assessment: apiRisk.initial_risk_assessment ?? '',
    root_cause_suggestion: apiRisk.root_cause_suggestion ?? '',
    capa_recommendation: apiRisk.capa_recommendation ?? '',
  };
}

/** Shape the form panel sends back to the commit endpoint. */
function toApiFormState(state) {
  const fields = {};
  Object.entries(state.fields).forEach(([key, value]) => {
    fields[key] = value === '' ? null : value;
  });

  const hasRisk = Boolean(state.riskAssessment.severity_suggested);
  return {
    fields,
    risk_assessment: hasRisk ? { ...state.riskAssessment } : null,
  };
}

// ---------------------------------------------------------------------------
// Thunks
// ---------------------------------------------------------------------------

/**
 * Start the app: resume the stored session if there is one, else create one.
 *
 * We pass the stored id to the backend, which returns that same session when
 * it is still open. That keeps React StrictMode's double effect invocation
 * from creating two sessions in development.
 */
export const bootSession = createAsyncThunk(
  'complaint/boot',
  async (_, { dispatch, rejectWithValue }) => {
    // Say something if the backend is slow to answer. A blank panel for a
    // minute reads as "this is broken"; "waking the server" reads as "wait".
    const notice = setTimeout(() => dispatch(serverWaking()), WAKE_NOTICE_MS);

    try {
      const storedId = localStorage.getItem(SESSION_STORAGE_KEY);

      if (storedId) {
        try {
          const restored = await api.fetchSession(storedId);
          if (!restored.committed) return { ...restored, restored: true };
        } catch {
          // Session was deleted or the DB was reset - fall through and start fresh.
        }
      }

      let created;
      try {
        created = await api.startSession(null);
      } catch (first) {
        // A host that is still starting can bounce the first request at its
        // proxy before the app is listening. One retry turns that into a
        // slightly slow load instead of an error screen on someone's first
        // ever visit. If the second attempt also fails, the failure is real.
        await sleep(BOOT_RETRY_MS);
        created = await api.startSession(null);
      }

      localStorage.setItem(SESSION_STORAGE_KEY, created.session_id);
      return { ...created, messages: [], restored: false };
    } catch (error) {
      return rejectWithValue(error.message);
    } finally {
      clearTimeout(notice);
    }
  }
);

/** Send pasted text or an uploaded file, and apply the assistant's turn. */
export const sendUserMessage = createAsyncThunk(
  'complaint/sendMessage',
  async ({ text, file }, { getState, rejectWithValue }) => {
    const { sessionId } = getState().complaint;
    try {
      return await api.sendMessage(sessionId, { text, file });
    } catch (error) {
      return rejectWithValue(error.message);
    }
  }
);

/** Commit the form the user is looking at, including any manual edits. */
export const commitToLedger = createAsyncThunk(
  'complaint/commit',
  async (_, { getState, rejectWithValue }) => {
    const state = getState().complaint;
    try {
      return await api.commitSession(state.sessionId, toApiFormState(state));
    } catch (error) {
      return rejectWithValue(error.message);
    }
  }
);

/** "Reset Form" - abandon this draft and open a clean session. */
export const resetSession = createAsyncThunk('complaint/reset', async (_, { rejectWithValue }) => {
  try {
    const created = await api.startSession(null);
    localStorage.setItem(SESSION_STORAGE_KEY, created.session_id);
    return created;
  } catch (error) {
    return rejectWithValue(error.message);
  }
});

// ---------------------------------------------------------------------------
// Slice
// ---------------------------------------------------------------------------

const initialState = {
  sessionId: null,
  status: 'pending_triage', // 'pending_triage' | 'ready_to_commit'
  committed: false,
  committedId: null,

  fields: { ...EMPTY_FIELDS },
  riskAssessment: { ...EMPTY_RISK },

  messages: [],
  changedFields: [], // drives the green highlight; cleared by a timer

  isBooting: true,
  isWakingServer: false, // boot is taking long enough to be worth explaining
  isSending: false,
  isCommitting: false,
  error: null,
};

/** Applied by every thunk that returns a form_state. */
function applyTurn(state, payload) {
  state.fields = normalizeFields(payload.form_state?.fields);
  state.riskAssessment = normalizeRisk(payload.form_state?.risk_assessment);
  state.status = payload.status ?? state.status;
}

const complaintSlice = createSlice({
  name: 'complaint',
  initialState,
  reducers: {
    /** A human editing the form by hand. Their value wins over the AI's. */
    updateField(state, action) {
      const { name, value } = action.payload;
      if (name in state.fields) {
        state.fields[name] = value;
      } else if (name in state.riskAssessment) {
        state.riskAssessment[name] = value;
      }
    },

    /** Called by a timer after the highlight has been visible long enough. */
    clearHighlights(state) {
      state.changedFields = [];
    },

    dismissError(state) {
      state.error = null;
    },

    /** Boot is slow - the hosted backend is probably still starting up. */
    serverWaking(state) {
      state.isWakingServer = true;
    },

    /** Optimistic echo, so the user's message appears instantly. */
    appendLocalMessage(state, action) {
      state.messages.push({
        role: action.payload.role,
        content: action.payload.content,
        timestamp: new Date().toISOString(),
      });
    },
  },

  extraReducers: (builder) => {
    builder
      // --- boot ---
      .addCase(bootSession.pending, (state) => {
        state.isBooting = true;
        state.isWakingServer = false;
        state.error = null;
      })
      .addCase(bootSession.fulfilled, (state, action) => {
        state.isBooting = false;
        state.isWakingServer = false;
        state.sessionId = action.payload.session_id;
        state.committed = action.payload.committed ?? false;
        applyTurn(state, action.payload);

        state.messages = action.payload.restored
          ? action.payload.messages ?? []
          : [{ role: 'assistant', content: action.payload.greeting, timestamp: new Date().toISOString() }];
      })
      .addCase(bootSession.rejected, (state, action) => {
        state.isBooting = false;
        state.isWakingServer = false;
        state.error = action.payload || 'Could not reach the server.';
      })

      // --- message turn ---
      .addCase(sendUserMessage.pending, (state) => {
        state.isSending = true;
        state.error = null;
        state.changedFields = [];
      })
      .addCase(sendUserMessage.fulfilled, (state, action) => {
        state.isSending = false;
        applyTurn(state, action.payload);
        // The whole point: the backend tells us exactly what to highlight.
        state.changedFields = action.payload.changed_fields ?? [];
        state.messages.push({
          role: 'assistant',
          content: action.payload.assistant_message,
          timestamp: new Date().toISOString(),
        });
      })
      .addCase(sendUserMessage.rejected, (state, action) => {
        state.isSending = false;
        state.error = action.payload || 'The message could not be sent.';
        // Surface transport failures in the chat too, so the conversation
        // reads as a continuous record of what happened.
        state.messages.push({
          role: 'assistant',
          content: `⚠ ${state.error}`,
          timestamp: new Date().toISOString(),
        });
      })

      // --- commit ---
      .addCase(commitToLedger.pending, (state) => {
        state.isCommitting = true;
        state.error = null;
      })
      .addCase(commitToLedger.fulfilled, (state, action) => {
        state.isCommitting = false;
        state.committed = true;
        state.committedId = action.payload.id;
        state.messages.push({
          role: 'assistant',
          content: `Complaint #${action.payload.id} has been committed to the QMS ledger.`,
          timestamp: new Date().toISOString(),
        });
      })
      .addCase(commitToLedger.rejected, (state, action) => {
        state.isCommitting = false;
        state.error = action.payload || 'The complaint could not be committed.';
      })

      // --- reset ---
      .addCase(resetSession.fulfilled, (state, action) => {
        state.sessionId = action.payload.session_id;
        state.status = 'pending_triage';
        state.committed = false;
        state.committedId = null;
        state.fields = { ...EMPTY_FIELDS };
        state.riskAssessment = { ...EMPTY_RISK };
        state.changedFields = [];
        state.error = null;
        state.messages = [
          { role: 'assistant', content: action.payload.greeting, timestamp: new Date().toISOString() },
        ];
      })

      // --- the user went away ---
      //
      // Two slices react to one action. Dropping the stored session id is the
      // point: it is keyed to the BROWSER, not the account, so without this
      // the next person to log in on a shared machine would resume the
      // previous user's half-finished complaint, chat history included.
      //
      // addMatcher rather than two addCases because RTK requires every
      // addCase to precede every addMatcher, and this needs to sit last.
      .addMatcher(
        (action) => action.type === logout.type || action.type === sessionExpired.type,
        () => {
          localStorage.removeItem(SESSION_STORAGE_KEY);
          // Returning a fresh object replaces the state wholesale. isBooting
          // is false because there is nothing to boot until someone logs in.
          return { ...initialState, isBooting: false };
        }
      );
  },
});

export const { updateField, clearHighlights, dismissError, appendLocalMessage, serverWaking } =
  complaintSlice.actions;

export default complaintSlice.reducer;

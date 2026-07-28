/**
 * Who is logged in.
 *
 * Kept separate from complaintSlice on purpose. The two have different
 * lifetimes - a session's form state is thrown away on every reset, while the
 * login survives all of that - and mixing them would mean every complaint
 * action had to be careful not to disturb the user.
 *
 * The TOKEN itself is not stored here. api/client.js owns it, because that is
 * the module that attaches it to requests and the module that has to drop it
 * when the server rejects it. This slice holds what the UI renders: the user,
 * and whether we are still finding out.
 */

import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import * as api from '../api/client';

/**
 * Restore a session from a stored token.
 *
 * Called once on load. The token is not trusted just because it is present -
 * it may be expired, or signed by a key the backend has since rotated - so
 * the only way to know is to ask. /auth/me answering 200 is the proof.
 *
 * A rejection here is the normal case for a first-time visitor, not an error
 * worth showing anyone, so it resolves to null rather than rejecting.
 */
export const loadCurrentUser = createAsyncThunk('auth/loadCurrentUser', async () => {
  if (!api.getAuthToken()) return null;

  try {
    return await api.fetchCurrentUser();
  } catch {
    // client.js has already cleared the token on a 401. Anything else - the
    // backend being asleep, say - also means we cannot show the app yet, and
    // the login page is the honest thing to render.
    return null;
  }
});

export const loginUser = createAsyncThunk(
  'auth/login',
  async ({ email, password }, { rejectWithValue }) => {
    try {
      const result = await api.login({ email, password });
      api.setAuthToken(result.access_token);
      return result.user;
    } catch (error) {
      return rejectWithValue(error.message);
    }
  }
);

export const signupUser = createAsyncThunk(
  'auth/signup',
  async ({ email, password, role }, { rejectWithValue }) => {
    try {
      const result = await api.signup({ email, password, role });
      api.setAuthToken(result.access_token);
      return result.user;
    } catch (error) {
      return rejectWithValue(error.message);
    }
  }
);

const initialState = {
  user: null, // { id, email, role, created_at }

  // True until loadCurrentUser has settled. Without it the app would flash
  // the login page for a moment on every refresh before /auth/me answers,
  // which looks like being logged out and is unpleasant enough that people
  // notice it.
  isRestoring: true,

  isSubmitting: false,
  error: null,
};

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    /** Explicit logout, from the button in the header. */
    logout(state) {
      api.clearAuthToken();
      state.user = null;
      state.error = null;
    },

    /**
     * The server rejected our token mid-session - it expired while the tab
     * was open, or an admin deleted the account.
     *
     * Wired to client.js's 401 handler in store/index.js, so ANY call that
     * comes back 401 lands the user on the login page rather than leaving
     * them clicking a UI that silently no longer works.
     */
    sessionExpired(state) {
      state.user = null;
      state.error = 'Your session has expired. Please log in again.';
    },

    dismissAuthError(state) {
      state.error = null;
    },
  },

  extraReducers: (builder) => {
    builder
      .addCase(loadCurrentUser.pending, (state) => {
        state.isRestoring = true;
      })
      .addCase(loadCurrentUser.fulfilled, (state, action) => {
        state.isRestoring = false;
        state.user = action.payload; // null when there was no usable token
      })
      .addCase(loadCurrentUser.rejected, (state) => {
        state.isRestoring = false;
        state.user = null;
      });

    // login and signup differ only in which endpoint they call, so they share
    // their reducers rather than having two identical copies.
    [loginUser, signupUser].forEach((thunk) => {
      builder
        .addCase(thunk.pending, (state) => {
          state.isSubmitting = true;
          state.error = null;
        })
        .addCase(thunk.fulfilled, (state, action) => {
          state.isSubmitting = false;
          state.user = action.payload;
          state.error = null;
        })
        .addCase(thunk.rejected, (state, action) => {
          state.isSubmitting = false;
          state.error = action.payload || 'Could not sign you in.';
        });
    });
  },
});

export const { logout, sessionExpired, dismissAuthError } = authSlice.actions;

export default authSlice.reducer;

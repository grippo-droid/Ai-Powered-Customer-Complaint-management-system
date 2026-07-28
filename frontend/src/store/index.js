import { configureStore } from '@reduxjs/toolkit';

import { setUnauthorizedHandler } from '../api/client';
import authReducer, { sessionExpired } from './authSlice';
import complaintReducer from './complaintSlice';

/**
 * One store, two slices: who is logged in, and what they are working on.
 *
 * configureStore wires up the Redux DevTools and the thunk middleware for us,
 * which is the practical reason to use Redux Toolkit rather than plain Redux.
 */
export const store = configureStore({
  reducer: {
    auth: authReducer,
    complaint: complaintReducer,
  },
  middleware: (getDefault) =>
    getDefault({
      // File objects are passed to the sendUserMessage thunk. They are not
      // serialisable and never enter the store - they go straight into a
      // FormData - so the check is disabled for that one action.
      serializableCheck: { ignoredActions: ['complaint/sendMessage/pending'] },
    }),
});

/**
 * Let the API client push the user back to the login screen.
 *
 * Any request answering 401 means the token is no longer good - it expired
 * while the tab sat open, or the account was removed - and every following
 * request would fail identically. Rather than let the user keep clicking a UI
 * that quietly no longer works, one 401 anywhere ends the session.
 *
 * Wired here, in the one place that already knows about both modules, so
 * client.js never has to import the store and no import cycle is created.
 */
setUnauthorizedHandler(() => store.dispatch(sessionExpired()));

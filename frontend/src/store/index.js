import { configureStore } from '@reduxjs/toolkit';

import complaintReducer from './complaintSlice';

/**
 * One store, one slice.
 *
 * configureStore wires up the Redux DevTools and the thunk middleware for us,
 * which is the practical reason to use Redux Toolkit rather than plain Redux.
 */
export const store = configureStore({
  reducer: {
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

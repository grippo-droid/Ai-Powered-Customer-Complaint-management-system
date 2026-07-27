import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';

import ChatPanel from './components/ChatPanel';
import ComplaintForm from './components/ComplaintForm';
import { bootSession, clearHighlights } from './store/complaintSlice';

/**
 * How long a changed field stays highlighted.
 *
 * Must match --highlight-duration in index.css: the CSS animates the fade,
 * this timer removes the class once the animation has finished.
 */
const HIGHLIGHT_MS = 2400;

export default function App() {
  const dispatch = useDispatch();
  const changedFields = useSelector((state) => state.complaint.changedFields);

  // React 18 StrictMode runs effects twice in development. The backend's
  // session endpoint is idempotent by id, but on a very first visit there is
  // no stored id yet, so two concurrent calls would still create two sessions.
  // A ref survives the StrictMode remount and makes boot happen exactly once.
  const hasBooted = useRef(false);

  useEffect(() => {
    if (hasBooted.current) return;
    hasBooted.current = true;
    dispatch(bootSession());
  }, [dispatch]);

  // Clear the highlight after the animation has played out. Depending on the
  // array itself means a new assistant turn restarts the timer.
  useEffect(() => {
    if (changedFields.length === 0) return undefined;
    const timer = setTimeout(() => dispatch(clearHighlights()), HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [changedFields, dispatch]);

  return (
    <div className="app">
      <ComplaintForm />
      <ChatPanel />
    </div>
  );
}

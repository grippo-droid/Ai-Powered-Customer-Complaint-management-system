import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';

import ChatPanel from './components/ChatPanel';
import ComplaintForm from './components/ComplaintForm';
import LoginPage from './components/LoginPage';
import { loadCurrentUser } from './store/authSlice';
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
  const isWakingServer = useSelector((state) => state.complaint.isWakingServer);
  const { user, isRestoring } = useSelector((state) => state.auth);

  // React 18 StrictMode runs effects twice in development, so both effects
  // below are guarded by refs rather than trusting the effect to run once.
  const hasCheckedAuth = useRef(false);

  // Which user we have already opened a complaint session for. Not a boolean:
  // if one person logs out and another logs in, this must boot again for the
  // new user rather than leaving them looking at nothing.
  const bootedForUserId = useRef(null);

  useEffect(() => {
    if (hasCheckedAuth.current) return;
    hasCheckedAuth.current = true;
    dispatch(loadCurrentUser());
  }, [dispatch]);

  useEffect(() => {
    if (!user) {
      bootedForUserId.current = null;
      return;
    }
    if (bootedForUserId.current === user.id) return;
    bootedForUserId.current = user.id;
    dispatch(bootSession());
  }, [user, dispatch]);

  // Clear the highlight after the animation has played out. Depending on the
  // array itself means a new assistant turn restarts the timer.
  useEffect(() => {
    if (changedFields.length === 0) return undefined;
    const timer = setTimeout(() => dispatch(clearHighlights()), HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [changedFields, dispatch]);

  // Held until /auth/me answers. Without this the login page flashes for a
  // moment on every refresh before the stored token is confirmed, which reads
  // as having been logged out.
  if (isRestoring) {
    return (
      <div className="auth-screen">
        <span className="spinner spinner-lg" />
      </div>
    );
  }

  // The whole of routing. There is exactly one decision - logged in or not -
  // and react-router would add a dependency, a bundle, and a layer of
  // indirection to express a boolean. If this app ever grows a second screen
  // worth linking to, that is the moment to add it.
  if (!user) {
    return <LoginPage />;
  }

  return (
    <div className="app">
      {/* Only ever seen on the hosted demo, whose free-tier container stops
          when idle. role="status" so a screen reader announces it without
          stealing focus. */}
      {isWakingServer && (
        <div className="wake-banner" role="status">
          <span className="spinner" />
          <span>
            Waking the demo server — it sleeps when idle, so this first load can take up to a
            minute. Nothing is broken.
          </span>
        </div>
      )}
      <ComplaintForm />
      <ChatPanel />
    </div>
  );
}

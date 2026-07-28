import { useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';

import { dismissAuthError, loginUser, signupUser } from '../store/authSlice';

/**
 * Login and signup, as one form with a toggle.
 *
 * The two differ by a single field, so two separate components would be
 * almost entirely duplicated markup. `mode` switches the heading, the button,
 * the endpoint, and whether the role selector is shown.
 */
export default function LoginPage() {
  const dispatch = useDispatch();
  const { isSubmitting, error } = useSelector((state) => state.auth);

  const [mode, setMode] = useState('login'); // 'login' | 'signup'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('qa_reviewer');

  const isSignup = mode === 'signup';

  // Mirrors the backend's floor (schemas.SignupRequest, security.hash_password).
  // Checking here too means the common mistake is caught without a round trip;
  // the server still enforces it, because a client-side check is a convenience,
  // never a control.
  const passwordTooShort = isSignup && password.length > 0 && password.length < 8;
  const canSubmit = email.trim() && password && !passwordTooShort && !isSubmitting;

  const submit = (event) => {
    event.preventDefault();
    if (!canSubmit) return;

    const credentials = { email: email.trim(), password };
    dispatch(isSignup ? signupUser({ ...credentials, role }) : loginUser(credentials));
  };

  const switchMode = () => {
    setMode(isSignup ? 'login' : 'signup');
    // Clearing the error matters: "Incorrect email or password" left hanging
    // over the signup form reads as though signup itself failed.
    dispatch(dismissAuthError());
  };

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <header className="auth-header">
          <h1 className="auth-title">Complaint Management System</h1>
          <p className="auth-subtitle">
            {isSignup ? 'Create an account to get started' : 'Sign in to continue'}
          </p>
        </header>

        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}

        <label className="auth-label" htmlFor="auth-email">
          Email
        </label>
        <input
          id="auth-email"
          className="auth-input"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@company.com"
          autoComplete="email"
          required
          disabled={isSubmitting}
        />

        <label className="auth-label" htmlFor="auth-password">
          Password
        </label>
        <input
          id="auth-password"
          className="auth-input"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder={isSignup ? 'At least 8 characters' : ''}
          // Tells a password manager whether to offer a saved password or to
          // generate a new one. Without it, signup gets autofilled with the
          // existing password, which is exactly the wrong suggestion.
          autoComplete={isSignup ? 'new-password' : 'current-password'}
          required
          disabled={isSubmitting}
        />
        {passwordTooShort && (
          <p className="auth-hint auth-hint-warn">Password must be at least 8 characters.</p>
        )}

        {isSignup && (
          <>
            <label className="auth-label" htmlFor="auth-role">
              Role
            </label>
            <select
              id="auth-role"
              className="auth-input"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              disabled={isSubmitting}
            >
              <option value="qa_reviewer">QA Reviewer — log and triage complaints</option>
              <option value="qa_lead">QA Lead — also commit to the QMS ledger</option>
            </select>
            <p className="auth-hint">
              Both roles do the full intake. Only a QA Lead can commit a complaint to the
              permanent ledger.
            </p>
          </>
        )}

        <button type="submit" className="btn btn-primary auth-submit" disabled={!canSubmit}>
          {isSubmitting ? <span className="spinner" /> : isSignup ? 'Create account' : 'Sign in'}
        </button>

        <p className="auth-switch">
          {isSignup ? 'Already have an account?' : 'No account yet?'}{' '}
          <button type="button" className="auth-link" onClick={switchMode} disabled={isSubmitting}>
            {isSignup ? 'Sign in' : 'Create one'}
          </button>
        </p>
      </form>
    </div>
  );
}

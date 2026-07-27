/* Placeholder - built in full in the final pass (chat panel). */
import { useSelector } from 'react-redux';

export default function ChatPanel() {
  const { sessionId, isBooting, error } = useSelector((state) => state.complaint);

  return (
    <section className="panel">
      <header className="panel-header">
        <h2 className="panel-title">AI Complaint Intake Assistant</h2>
        <span className="badge badge-beta">BETA</span>
      </header>
      <div className="panel-body">
        {isBooting && <p style={{ color: 'var(--text-muted)' }}>Connecting to the server...</p>}
        {error && <div className="alert">{error}</div>}
        {sessionId && (
          <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            Session <code>{sessionId}</code> is open. Chat UI is added in the next pass.
          </p>
        )}
      </div>
      <p className="disclaimer">AI responses may contain errors, please verify.</p>
    </section>
  );
}

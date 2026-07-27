import { useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';

import FileDropzone from './FileDropzone';
import MessageList from './MessageList';
import { appendLocalMessage, sendUserMessage } from '../store/complaintSlice';

/**
 * The right panel: the AI intake assistant.
 *
 * Both inputs - pasted text and an uploaded file - go to the same thunk and
 * therefore the same backend endpoint. The only difference is what we echo
 * into the transcript before the request goes out.
 */
export default function ChatPanel() {
  const dispatch = useDispatch();
  const { messages, isSending, isBooting, committed, sessionId } = useSelector(
    (state) => state.complaint
  );
  const [draft, setDraft] = useState('');

  const busy = isSending || isBooting || committed || !sessionId;

  const submitText = () => {
    const text = draft.trim();
    if (!text || busy) return;
    // Echo the user's message immediately so the UI feels responsive while
    // the graph runs. The backend records the same message in its history.
    dispatch(appendLocalMessage({ role: 'user', content: text }));
    dispatch(sendUserMessage({ text, file: null }));
    setDraft('');
  };

  const submitFile = (file) => {
    if (busy) return;
    dispatch(appendLocalMessage({ role: 'user', content: `📎 ${file.name}` }));
    dispatch(sendUserMessage({ text: '', file }));
  };

  const handleKeyDown = (event) => {
    // Enter sends, Shift+Enter makes a new line - the convention users expect
    // from every other chat interface.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submitText();
    }
  };

  return (
    <section className="panel">
      <header className="panel-header">
        <div>
          <h2 className="panel-title">AI Complaint Intake Assistant</h2>
          <p className="panel-subtitle">Upload a document or describe the complaint</p>
        </div>
      </header>

      <div className="panel-body">
        <FileDropzone onFile={submitFile} disabled={busy} />
        <MessageList messages={messages} isSending={isSending} />
      </div>

      <div className="chat-composer">
        <textarea
          className="chat-textarea"
          rows={2}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            committed
              ? 'This complaint has been committed. Reset the form to start a new one.'
              : 'Paste a complaint email, or type a correction...'
          }
          disabled={busy}
        />
        <button
          type="button"
          className="btn btn-primary btn-icon"
          onClick={submitText}
          disabled={busy || !draft.trim()}
          aria-label="Send message"
        >
          {isSending ? <span className="spinner" /> : '➤'}
        </button>
      </div>

      <p className="disclaimer">AI responses may contain errors, please verify.</p>
    </section>
  );
}

import { useEffect, useRef } from 'react';

/**
 * The chat transcript.
 *
 * Assistant messages come from format_output, which writes field names in
 * **bold** so a correction confirmation is easy to scan. renderBold below is
 * a deliberately tiny substitute for a markdown library - the backend only
 * ever emits `**...**`, so pulling in a parser would be more code to explain
 * than the four lines it replaces.
 */
export default function MessageList({ messages, isSending }) {
  const endRef = useRef(null);

  // Keep the newest message in view as the conversation grows.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, isSending]);

  return (
    <div className="chat-messages">
      {messages.map((message, index) => (
        <div
          key={`${message.timestamp}-${index}`}
          className={`message message-${message.role}`}
        >
          <span className="message-role">
            {message.role === 'assistant' ? 'AI Assistant' : 'You'}
          </span>
          {renderBold(message.content)}
        </div>
      ))}

      {isSending && (
        <div className="message message-assistant message-thinking">
          <span className="spinner" />
          Analysing and updating the form...
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}

/** Turn `a **b** c` into ['a ', <strong>b</strong>, ' c']. */
function renderBold(text = '') {
  return text.split(/\*\*(.+?)\*\*/g).map((chunk, index) =>
    index % 2 === 1 ? <strong key={index}>{chunk}</strong> : chunk
  );
}

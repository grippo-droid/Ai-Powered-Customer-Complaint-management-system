import { useRef, useState } from 'react';

const ACCEPTED = '.pdf,.docx,.txt,.eml';

/**
 * Drag-and-drop or click-to-browse upload.
 *
 * Deliberately does no validation of its own. The backend already checks the
 * extension and the size limit, and it returns a readable message in the chat
 * when a file is rejected. Duplicating those rules here would mean two places
 * to keep in sync and two chances to disagree.
 */
export default function FileDropzone({ onFile, disabled }) {
  const inputRef = useRef(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    const file = event.dataTransfer.files?.[0];
    if (file) onFile(file);
  };

  const handleSelect = (event) => {
    const file = event.target.files?.[0];
    if (file) onFile(file);
    // Reset so selecting the same file twice still fires a change event.
    event.target.value = '';
  };

  return (
    <div
      className={`dropzone${isDragging ? ' is-dragging' : ''}`}
      onClick={() => !disabled && inputRef.current?.click()}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click();
      }}
    >
      <span className="dropzone-title">Drag &amp; drop complaint document here</span>
      <span className="dropzone-hint">or click to browse</span>
      <span className="dropzone-hint">Supported: PDF, DOCX, TXT, EML &nbsp;·&nbsp; Max 10 MB</span>

      <input
        ref={inputRef}
        type="file"
        className="visually-hidden"
        accept={ACCEPTED}
        onChange={handleSelect}
        disabled={disabled}
      />
    </div>
  );
}

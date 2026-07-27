import { useDispatch } from 'react-redux';

import { updateField } from '../store/complaintSlice';

/**
 * One labelled form control.
 *
 * Two things every field does:
 *
 *  1. It is CONTROLLED by Redux. The AI populates it by writing to the store;
 *     the user editing it dispatches updateField. There is no local component
 *     state, so an AI turn and a human edit go through the same path.
 *
 *  2. When its name is in `changed_fields` from the last assistant turn, it
 *     gets the `field-changed` class and the CSS flashes it green. That is
 *     the entire highlight mechanism on the client side.
 */
export default function Field({
  name,
  label,
  value,
  changed = false,
  type = 'text',
  options = null,
  placeholder = 'Awaiting AI extraction...',
  rows = null,
  full = false,
  disabled = false,
}) {
  const dispatch = useDispatch();
  const handleChange = (event) => dispatch(updateField({ name, value: event.target.value }));

  const className = [
    'field',
    full ? 'field-full' : '',
    changed ? 'field-changed' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={className}>
      <label className="field-label" htmlFor={name}>
        {label}
      </label>

      {options ? (
        <select
          id={name}
          className="field-select"
          value={value}
          onChange={handleChange}
          disabled={disabled}
        >
          <option value="">Awaiting AI extraction...</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : rows ? (
        <textarea
          id={name}
          className="field-textarea"
          value={value}
          onChange={handleChange}
          placeholder={placeholder}
          rows={rows}
          disabled={disabled}
        />
      ) : (
        <input
          id={name}
          className="field-input"
          type={type}
          value={value}
          onChange={handleChange}
          placeholder={type === 'date' ? undefined : placeholder}
          disabled={disabled}
        />
      )}
    </div>
  );
}

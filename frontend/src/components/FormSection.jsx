/**
 * A numbered form section: "1  ORIGIN & CUSTOMER DETAILS" plus its grid.
 *
 * `className` is optional and lets one section opt into extra styling without
 * affecting the others - section 4 uses it to become the AI Copilot card.
 */
export default function FormSection({ number, title, className = '', children }) {
  return (
    <section className={`section ${className}`.trim()}>
      <h3 className="section-title">
        <span className="section-number">{number}</span>
        {title}
      </h3>
      <div className="field-grid">{children}</div>
    </section>
  );
}

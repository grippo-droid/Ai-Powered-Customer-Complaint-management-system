/** A numbered form section: "1  ORIGIN & CUSTOMER DETAILS" plus its grid. */
export default function FormSection({ number, title, children }) {
  return (
    <section className="section">
      <h3 className="section-title">
        <span className="section-number">{number}</span>
        {title}
      </h3>
      <div className="field-grid">{children}</div>
    </section>
  );
}

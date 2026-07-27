/* Placeholder - built in full in the next pass (form panel). */
export default function ComplaintForm() {
  return (
    <section className="panel">
      <header className="panel-header">
        <div>
          <h1 className="panel-title">Log Customer Complaint</h1>
          <p className="panel-subtitle">API &amp; FDF Quality Assurance Module</p>
        </div>
        <span className="badge badge-pending">Pending Triage</span>
      </header>
      <div className="panel-body">
        <p style={{ color: 'var(--text-muted)' }}>Form fields are added in the next pass.</p>
      </div>
    </section>
  );
}

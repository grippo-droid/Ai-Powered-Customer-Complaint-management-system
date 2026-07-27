import { useDispatch, useSelector } from 'react-redux';

import Field from './Field';
import FormSection from './FormSection';
import { commitToLedger, resetSession } from '../store/complaintSlice';

/**
 * The left panel: the complaint record itself.
 *
 * Every value comes from Redux. The AI writes to the store from the chat
 * panel, the reviewer can type over anything, and whichever version is on
 * screen at commit time is what gets written to the ledger.
 */
export default function ComplaintForm() {
  const dispatch = useDispatch();
  const { fields, riskAssessment, changedFields, status, committed, committedId, isCommitting } =
    useSelector((state) => state.complaint);

  // Membership test used by every field below. changed_fields uses the same
  // flat names as the form, so no mapping table is needed.
  const isChanged = (name) => changedFields.includes(name);

  const handleReset = () => {
    if (window.confirm('Clear the form and start a new complaint?')) {
      dispatch(resetSession());
    }
  };

  return (
    <section className="panel">
      <header className="panel-header">
        <div>
          <h1 className="panel-title">Log Customer Complaint</h1>
          <p className="panel-subtitle">API &amp; FDF Quality Assurance Module</p>
        </div>
        <StatusBadge status={status} committed={committed} committedId={committedId} />
      </header>

      <div className="panel-body">
        <FormSection number="1" title="Origin &amp; Customer Details">
          <Field
            name="complaint_source"
            label="Complaint Source"
            value={fields.complaint_source}
            changed={isChanged('complaint_source')}
            options={['Pharmacy', 'Email']}
            disabled={committed}
          />
          <Field
            name="customer_name"
            label="Customer Name"
            value={fields.customer_name}
            changed={isChanged('customer_name')}
            disabled={committed}
          />
        </FormSection>

        <FormSection number="2" title="Product &amp; Batch Identification">
          <Field
            name="product_name"
            label="Product Name"
            value={fields.product_name}
            changed={isChanged('product_name')}
            disabled={committed}
          />
          <Field
            name="product_strength"
            label="Product Strength/Grade"
            value={fields.product_strength}
            changed={isChanged('product_strength')}
            disabled={committed}
          />
          <Field
            name="batch_number"
            label="Batch/Lot Number"
            value={fields.batch_number}
            changed={isChanged('batch_number')}
            disabled={committed}
          />
          <Field
            name="affected_quantity"
            label="Affected Quantity"
            value={fields.affected_quantity}
            changed={isChanged('affected_quantity')}
            disabled={committed}
          />
          {/* type="date" needs YYYY-MM-DD, which is exactly what the date
              validator in schemas.py normalises to. When a source document
              had an unparseable date we keep the raw string instead, so these
              two fall back to a plain text input rather than silently showing
              an empty date picker. */}
          <Field
            name="manufacturing_date"
            label="Manufacturing Date"
            value={fields.manufacturing_date}
            changed={isChanged('manufacturing_date')}
            type={isIsoDate(fields.manufacturing_date) ? 'date' : 'text'}
            disabled={committed}
          />
          <Field
            name="expiry_date"
            label="Expiry Date"
            value={fields.expiry_date}
            changed={isChanged('expiry_date')}
            type={isIsoDate(fields.expiry_date) ? 'date' : 'text'}
            disabled={committed}
          />
        </FormSection>

        <FormSection number="3" title="Complaint Details">
          <Field
            name="complaint_type"
            label="Complaint Type"
            value={fields.complaint_type}
            changed={isChanged('complaint_type')}
            disabled={committed}
          />
          <Field
            name="complaint_date"
            label="Complaint Date"
            value={fields.complaint_date}
            changed={isChanged('complaint_date')}
            type={isIsoDate(fields.complaint_date) ? 'date' : 'text'}
            disabled={committed}
          />
          <Field
            name="complaint_description"
            label="Detailed Complaint Description"
            value={fields.complaint_description}
            changed={isChanged('complaint_description')}
            rows={4}
            full
            disabled={committed}
          />
        </FormSection>

        <FormSection number="4" title="AI Copilot Risk Assessment">
          <Field
            name="severity_suggested"
            label="Severity (Suggested)"
            value={riskAssessment.severity_suggested}
            changed={isChanged('severity_suggested')}
            placeholder="Awaiting AI assessment..."
            disabled={committed}
          />
          <Field
            name="suggested_next_action"
            label="Suggested Next Action"
            value={riskAssessment.suggested_next_action}
            changed={isChanged('suggested_next_action')}
            placeholder="Awaiting AI assessment..."
            disabled={committed}
          />
          <Field
            name="initial_risk_assessment"
            label="Initial Risk Assessment"
            value={riskAssessment.initial_risk_assessment}
            changed={isChanged('initial_risk_assessment')}
            placeholder="Awaiting AI assessment..."
            rows={3}
            full
            disabled={committed}
          />
        </FormSection>
      </div>

      <footer className="panel-footer">
        <button type="button" className="btn btn-secondary" onClick={handleReset}>
          Reset Form
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => dispatch(commitToLedger())}
          disabled={committed || isCommitting || status !== 'ready_to_commit'}
          title={
            status === 'ready_to_commit'
              ? 'Write this complaint to the QMS ledger'
              : 'Product Name, Batch Number, Description and an AI assessment are required first'
          }
        >
          {isCommitting && <span className="spinner" />}
          {committed ? 'Committed' : 'Commit to QMS Ledger'}
        </button>
      </footer>
    </section>
  );
}

/** True for 'YYYY-MM-DD', which is what <input type="date"> requires. */
function isIsoDate(value) {
  return !value || /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function StatusBadge({ status, committed, committedId }) {
  if (committed) {
    return <span className="badge badge-committed">Committed #{committedId}</span>;
  }
  if (status === 'ready_to_commit') {
    return <span className="badge badge-ready">Ready to Commit</span>;
  }
  return <span className="badge badge-pending">Pending Triage</span>;
}

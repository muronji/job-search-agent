import { useEffect, useState } from 'react';
import { apiFetch } from '../api';

const STATUS_LABELS = {
  suggested: 'Suggested',
  no_supported_board: 'No supported board',
  found: 'Found',
  approved: 'Approved',
  rejected: 'Rejected',
};

function formatDate(value) {
  if (!value) return 'Never';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function SourceHealthTable({ status }) {
  if (!status || status.length === 0) {
    return <p className="hint">No source health data yet.</p>;
  }
  return (
    <table className="source-table">
      <thead>
        <tr>
          <th>Source</th>
          <th>Active</th>
          <th>Jobs</th>
          <th>Expired</th>
          <th>Last ingest</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {status.map((source) => (
          <tr key={source.id}>
            <td>{source.name}</td>
            <td>{source.is_active ? 'Yes' : 'No'}</td>
            <td>{source.job_count}</td>
            <td>{source.expired_job_count}</td>
            <td>{formatDate(source.last_ingested_at)}</td>
            <td>{source.last_ingestion_status || 'Unknown'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Employers() {
  const [candidates, setCandidates] = useState([]);
  const [sources, setSources] = useState([]);
  const [sourceStatus, setSourceStatus] = useState([]);
  const [showHealth, setShowHealth] = useState(false);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    loadCandidates();
    loadSources();
    loadSourceStatus();
  }, []);

  async function loadCandidates() {
    setLoading(true);
    setError('');
    try {
      const data = await apiFetch('/api/employer-candidates/');
      setCandidates(Array.isArray(data) ? data : data.results || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadSources() {
    try {
      const data = await apiFetch('/api/sources/');
      setSources(Array.isArray(data) ? data : data.results || []);
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadSourceStatus() {
    try {
      const data = await apiFetch('/api/sources/status/');
      setSourceStatus(data);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleSuggest(force = false) {
    setError('');
    setMessage('');
    setLoading(true);
    try {
      const data = await apiFetch('/api/employer-candidates/suggest/', {
        method: 'POST',
        body: JSON.stringify({ force }),
      });
      setCandidates(Array.isArray(data) ? data : data.results || []);
      setMessage(`Suggested ${(Array.isArray(data) ? data : data.results || []).length} employer(s).`);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify(id) {
    setError('');
    setMessage('');
    try {
      await apiFetch(`/api/employer-candidates/${id}/verify/`, { method: 'POST' });
      await loadCandidates();
      setMessage('Candidate verified.');
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleApprove(id) {
    setError('');
    setMessage('');
    try {
      await apiFetch(`/api/employer-candidates/${id}/approve/`, { method: 'POST' });
      await loadCandidates();
      await loadSources();
      await loadSourceStatus();
      setMessage('Candidate approved and added to shared sources.');
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleReject(id) {
    setError('');
    setMessage('');
    try {
      await apiFetch(`/api/employer-candidates/${id}/reject/`, { method: 'POST' });
      await loadCandidates();
      setMessage('Candidate rejected.');
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleScanNow() {
    setError('');
    setMessage('');
    setScanning(true);
    try {
      const data = await apiFetch('/api/employer-candidates/scan_now/', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      const created = data.results.reduce(
        (sum, r) => sum + (r.summary?.created || 0),
        0
      );
      setMessage(`Scan complete. Created ${created} job(s) from ${data.results.length} source(s).`);
      await loadSources();
      await loadSourceStatus();
    } catch (err) {
      setError(err.message);
    } finally {
      setScanning(false);
    }
  }

  return (
    <section aria-label="Employers">
      <h2>Employers</h2>
      {error && <p className="error-message" role="alert">{error}</p>}
      {message && <p className="success-message" role="status">{message}</p>}

      <div className="button-row">
        <button
          type="button"
          className="primary"
          onClick={() => handleSuggest(false)}
          disabled={loading}
        >
          Suggest employers
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => handleSuggest(true)}
          disabled={loading}
        >
          Refresh suggestions
        </button>
        <button
          type="button"
          className="primary"
          onClick={handleScanNow}
          disabled={scanning}
        >
          {scanning ? 'Scanning…' : 'Scan now'}
        </button>
      </div>

      <p className="hint">
        Suggestions are unverified claims from your profile. Verify probes public
        ATS boards; approve creates a shared source; scan now imports listings
        from your approved sources.
      </p>

      <div className="source-status-collapsible">
        <button
          type="button"
          className="collapsible-toggle"
          onClick={() => setShowHealth((prev) => !prev)}
          aria-expanded={showHealth}
        >
          Source health {showHealth ? '▲' : '▼'}
        </button>
        {showHealth && (
          <div className="collapsible-content">
            <SourceHealthTable status={sourceStatus} />
          </div>
        )}
      </div>

      {loading && candidates.length === 0 && <p>Loading employers…</p>}

      <div className="candidate-list">
        {candidates.map((candidate) => (
          <article key={candidate.id} className="candidate-card">
            <div className="candidate-header">
              <div>
                <h3>{candidate.display_name}</h3>
                <p className="candidate-meta">
                  Status: {STATUS_LABELS[candidate.status] || candidate.status}
                  {candidate.adapter_key && ` • ${candidate.adapter_key}`}
                  {candidate.board_value && `:${candidate.board_value}`}
                </p>
                {candidate.reason && (
                  <p className="candidate-reason">{candidate.reason}</p>
                )}
                {candidate.sample_jobs && candidate.sample_jobs.length > 0 && (
                  <div className="sample-jobs">
                    <p className="sample-label">Sample jobs:</p>
                    <ul>
                      {candidate.sample_jobs.map((job, i) => (
                        <li key={i}>
                          {job.title}
                          {job.location && ` — ${job.location}`}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <p className="candidate-meta">
                  Last probed: {formatDate(candidate.last_probed_at)}
                </p>
              </div>
              <div className="candidate-actions">
                {candidate.status === 'suggested' && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => handleVerify(candidate.id)}
                    disabled={loading}
                  >
                    Verify
                  </button>
                )}
                {candidate.status === 'found' && (
                  <>
                    <button
                      type="button"
                      className="primary"
                      onClick={() => handleApprove(candidate.id)}
                      disabled={loading}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => handleReject(candidate.id)}
                      disabled={loading}
                    >
                      Reject
                    </button>
                  </>
                )}
                {candidate.status === 'no_supported_board' && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => handleVerify(candidate.id)}
                    disabled={loading}
                  >
                    Retry
                  </button>
                )}
              </div>
            </div>
          </article>
        ))}
      </div>

      <div className="source-status">
        <h3>Shared sources</h3>
        {sources.length === 0 ? (
          <p className="hint">No shared sources yet. Approve verified candidates to create them.</p>
        ) : (
          <table className="source-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Active</th>
                <th>Last ingest</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => (
                <tr key={source.id}>
                  <td>{source.name}</td>
                  <td>{source.is_active ? 'Yes' : 'No'}</td>
                  <td>{formatDate(source.last_ingested_at)}</td>
                  <td>{source.last_ingestion_status || 'Unknown'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

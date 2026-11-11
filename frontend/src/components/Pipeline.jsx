import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../api';

const STATUS_ORDER = [
  { key: 'saved', label: 'Saved' },
  { key: 'applied', label: 'Applied' },
  { key: 'interviewing', label: 'Interviewing' },
  { key: 'offered', label: 'Offered' },
  { key: 'rejected', label: 'Rejected' },
  { key: 'withdrawn', label: 'Withdrawn' },
  { key: 'closed', label: 'Closed' },
  { key: 'hired', label: 'Hired' },
];

function displayChoice(value) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function Pipeline() {
  const [userJobs, setUserJobs] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    setError('');
    try {
      const [userJobsData, jobsData] = await Promise.all([
        apiFetch('/api/user-jobs/'),
        apiFetch('/api/jobs/'),
      ]);
      setUserJobs(Array.isArray(userJobsData) ? userJobsData : userJobsData.results || []);
      setJobs(Array.isArray(jobsData) ? jobsData : jobsData.results || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const jobMap = useMemo(() => {
    const map = {};
    jobs.forEach((job) => {
      map[job.id] = job;
    });
    return map;
  }, [jobs]);

  const columns = useMemo(() => {
    return STATUS_ORDER.map((status) => ({
      ...status,
      items: userJobs.filter((uj) => uj.application_status === status.key),
    }));
  }, [userJobs]);

  async function changeStatus(userJobId, newStatus) {
    setError('');
    try {
      const data = await apiFetch(`/api/user-jobs/${userJobId}/`, {
        method: 'PATCH',
        body: JSON.stringify({ application_status: newStatus }),
      });
      setUserJobs((prev) => prev.map((uj) => (uj.id === data.id ? data : uj)));
    } catch (err) {
      setError(err.message);
    }
  }

  if (loading && userJobs.length === 0) {
    return <p>Loading pipeline…</p>;
  }

  return (
    <section aria-label="Pipeline" className="pipeline-screen">
      <h2>Pipeline</h2>
      {error && <p className="error-message" role="alert">{error}</p>}
      <p className="hint">
        This board uses the application status on each tracked job. There is no separate stage field yet.
      </p>
      <div className="kanban">
        {columns.map((column) => (
          <div key={column.key} className="kanban-column">
            <h4>
              {column.label} ({column.items.length})
            </h4>
            {column.items.map((uj) => {
              const job = jobMap[uj.job];
              return (
                <div key={uj.id} className="kanban-card">
                  <h5>{job ? job.title : `Job ${uj.job}`}</h5>
                  <p>{job ? job.company : 'Unknown employer'}</p>
                  {uj.notes && <p>Notes: {uj.notes}</p>}
                  <select
                    value={uj.application_status}
                    onChange={(e) => changeStatus(uj.id, e.target.value)}
                    aria-label="Move to stage"
                  >
                    {STATUS_ORDER.map((s) => (
                      <option key={s.key} value={s.key}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}

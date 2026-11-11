import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../api';

const VERDICT_TILES = [
  { key: 'apply', label: 'Apply', kind: 'verdict' },
  { key: 'track', label: 'Track', kind: 'verdict' },
  { key: 'ignore', label: 'Ignore', kind: 'verdict' },
  { key: 'saved', label: 'Saved', kind: 'saved' },
];

const MORE_FILTERS = [
  { key: 'seniority', label: 'Seniority' },
  { key: 'employment_type', label: 'Employment type' },
  { key: 'source_listing_status', label: 'Source listing status' },
];

const STATUS_ORDER = [
  'saved',
  'applied',
  'interviewing',
  'offered',
  'rejected',
  'withdrawn',
  'closed',
  'hired',
];

function buildQuery(params) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== '' && value !== null && value !== undefined && value !== false) {
      qs.append(key, value);
    }
  });
  return qs.toString();
}

function isHttpUrl(value) {
  if (!value) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function formatDate(value) {
  if (!value) return 'Unknown';
  try {
    return new Date(value).toLocaleDateString();
  } catch {
    return value;
  }
}

function displayChoice(value) {
  if (!value) return value;
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function hasPreferences(profile) {
  if (!profile?.parsed_profile) return false;
  const p = profile.parsed_profile;
  const prefs = p.preferences || {};
  return (
    (p.skills?.length > 0) ||
    (p.experience?.length > 0) ||
    (p.education?.length > 0) ||
    Boolean(p.location) ||
    Boolean(p.headline) ||
    (prefs.job_titles?.length > 0) ||
    (prefs.locations?.length > 0) ||
    (prefs.industries?.length > 0) ||
    (prefs.seniority_levels?.length > 0) ||
    (prefs.employment_types?.length > 0) ||
    (prefs.work_arrangements?.length > 0) ||
    prefs.min_salary != null
  );
}

function isRemote(job) {
  return (
    job.work_arrangement === 'remote' ||
    /\bremote\b/i.test(job.location || '')
  );
}

function classNames(...parts) {
  return parts.filter(Boolean).join(' ');
}

function CriteriaChips({ matched, unmatched, unknown, access, level, gates }) {
  return (
    <div className="criteria-chips">
      {(matched || []).map((item) => (
        <span key={`m-${item}`} className="chip chip-match">{item}</span>
      ))}
      {(unmatched || []).map((item) => (
        <span key={`u-${item}`} className="chip chip-unmatch">{item}</span>
      ))}
      {(unknown || []).map((item) => (
        <span key={`x-${item}`} className="chip chip-unknown">{item}</span>
      ))}
      {access && <span className="chip chip-access">{access}</span>}
      {level && <span className="chip chip-level">{level}</span>}
      {(gates || []).map((item) => (
        <span key={`g-${item}`} className="chip chip-gate">{item}</span>
      ))}
    </div>
  );
}

function ScoreRing({ total, verdict }) {
  const ringClass = verdict && verdict !== 'not_scored' ? `ring-${verdict}` : 'ring-not_scored';
  return (
    <div className={classNames('score-ring', ringClass)} aria-label={`Score ${total}`}>
      <span className="score-ring-value">{total ?? '–'}</span>
      <span className="score-ring-max">/125</span>
    </div>
  );
}

function JobCard({ job, sourceMap, userJob, onSave, onNotInterested }) {
  const [expanded, setExpanded] = useState(false);
  const sourceName = job.source ? sourceMap[job.source] || `Source ${job.source}` : 'Manual';
  const isRemoteOk = sourceName === 'Remote OK';
  const scored = job.verdict && job.verdict !== 'not_scored';
  const saved = userJob?.application_status === 'saved';
  const closed = userJob?.application_status === 'closed';
  const remote = isRemote(job);
  const description = job.description || '';

  const closingSoon = job.closing_soon && job.application_deadline;

  return (
    <article className="job-card">
      <div className="job-card-header">
        <div className="job-card-title-wrap">
          <h3 className="job-card-title">{job.title || 'Untitled role'}</h3>
          <div className="job-card-meta">
            <span className="job-card-employer">{job.company || 'Unknown employer'}</span>
            <span className="job-card-location">{job.location || 'No location'}</span>
            {remote && <span className="remote-badge">Remote</span>}
            {job.is_confirmed && <span className="confirmed-badge">Confirmed</span>}
            {job.is_lead && <span className="lead-badge">Lead</span>}
            {closingSoon && <span className="closing-badge">Closing soon</span>}
          </div>
        </div>
        <div className="job-card-score">
          <ScoreRing total={job.total_score} verdict={job.verdict} />
          <div className={classNames('verdict-badge', scored ? `verdict-${job.verdict}` : 'verdict-not_scored')}>
            <div className="verdict-label">{scored ? displayChoice(job.verdict) : 'Not scored'}</div>
          </div>
        </div>
      </div>

      {scored && (
        <div className="sub-scores">
          <span className="sub-score">Fit {job.fit_score ?? '–'}/5</span>
          <span className="sub-score">Access {job.access_score ?? '–'}/5</span>
          <span className="sub-score">Level {job.level_score ?? '–'}/5</span>
        </div>
      )}

      {description && (
        <div className="job-description-wrap">
          <p className={classNames('job-description', !expanded && 'clamped')}>
            {description}
          </p>
          {description.length > 180 && (
            <button
              type="button"
              className="text-button"
              onClick={() => setExpanded((prev) => !prev)}
            >
              {expanded ? 'Show less' : 'Show more'}
            </button>
          )}
        </div>
      )}

      <CriteriaChips
        matched={job.matched_criteria}
        unmatched={job.unmatched_criteria}
        unknown={job.unknown_criteria}
        access={job.access_reason}
        level={job.level_reason}
        gates={job.gate_reasons}
      />

      <div className="job-actions">
        <button
          type="button"
          className={classNames('action-button', saved && 'active')}
          onClick={() => onSave(job.id)}
          disabled={saved}
          aria-pressed={saved}
        >
          {saved ? 'Saved' : 'Save'}
        </button>
        <button
          type="button"
          className={classNames('action-button', closed && 'active')}
          onClick={() => onNotInterested(job.id)}
          disabled={closed}
          aria-pressed={closed}
        >
          {closed ? 'Not interested' : 'Not interested'}
        </button>
        {isHttpUrl(job.source_url) && (
          <a
            className="action-link"
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {isRemoteOk ? 'via Remote OK' : 'View posting'}
          </a>
        )}
      </div>

      <p className="job-source-line">
        {sourceName} • Fetched {formatDate(job.date_discovered)}
      </p>
    </article>
  );
}

function Onboarding({ profile, candidates, sources, jobs, totalJobs }) {
  const step1 = hasPreferences(profile);
  const step2 = candidates.length > 0 || sources.length > 0;
  const step3 = totalJobs > 0 && jobs.length > 0;
  const allDone = step1 && step2 && step3;
  if (allDone) return null;

  return (
    <div className="onboarding">
      <h3>Get started</h3>
      <div className="onboarding-steps">
        <div className={classNames('onboarding-step', step1 && 'done')}>
          <span className="step-number">{step1 ? '✓' : '1'}</span>
          <div>
            <strong>Add your CV or preferences</strong>
            {!step1 && <p>So the agent can score roles for you.</p>}
          </div>
        </div>
        <div className={classNames('onboarding-step', step2 && 'done')}>
          <span className="step-number">{step2 ? '✓' : '2'}</span>
          <div>
            <strong>Find employers</strong>
            {!step2 && <p>Approve sources on the Employers tab.</p>}
          </div>
        </div>
        <div className={classNames('onboarding-step', step3 && 'done')}>
          <span className="step-number">{step3 ? '✓' : '3'}</span>
          <div>
            <strong>Review your roles</strong>
            {!step3 && <p>Jobs will appear here after a scan.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}

export function Roles() {
  const [jobs, setJobs] = useState([]);
  const [count, setCount] = useState(0);
  const [nextUrl, setNextUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sort, setSort] = useState('best');
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState({
    seniority: '',
    employment_type: '',
    work_arrangement: '',
    application_status: '',
    source_listing_status: '',
  });
  const [goneOnly, setGoneOnly] = useState(false);
  const [verdictFilter, setVerdictFilter] = useState('');
  const [countryFilter, setCountryFilter] = useState('');
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [bestMatchMode, setBestMatchMode] = useState(false);
  const [savedFilter, setSavedFilter] = useState(false);
  const [summary, setSummary] = useState(null);
  const [filterOptions, setFilterOptions] = useState({});
  const [sources, setSources] = useState([]);
  const [sourceStatus, setSourceStatus] = useState([]);
  const [userJobs, setUserJobs] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [profile, setProfile] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [verdictCounts, setVerdictCounts] = useState({
    apply: 0,
    track: 0,
    ignore: 0,
    not_scored: 0,
  });

  const sourceMap = useMemo(() => {
    const map = {};
    sources.forEach((s) => {
      map[s.id] = s.name;
    });
    return map;
  }, [sources]);

  const userJobsMap = useMemo(() => {
    const map = {};
    userJobs.forEach((uj) => {
      map[uj.job] = uj;
    });
    return map;
  }, [userJobs]);

  const countryOptions = useMemo(() => {
    const counts = coverage?.country_counts || {};
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([country]) => country);
  }, [coverage]);

  const savedCount = useMemo(
    () => userJobs.filter((uj) => uj.application_status === 'saved').length,
    [userJobs]
  );

  const activeFilterLabels = useMemo(() => {
    const labels = [];
    if (sort === 'best') labels.push('Best match');
    else labels.push('Newest');
    if (query) labels.push(`Search: "${query}"`);
    if (bestMatchMode && sort === 'best') labels.push('Best matches (Apply & Track)');
    if (verdictFilter) labels.push(`Verdict: ${displayChoice(verdictFilter)}`);
    if (savedFilter) labels.push('Saved');
    if (remoteOnly) labels.push('Remote');
    if (countryFilter) labels.push(`Country: ${countryFilter}`);
    MORE_FILTERS.forEach(({ key, label }) => {
      if (filters[key]) labels.push(`${label}: ${displayChoice(filters[key])}`);
    });
    if (filters.application_status) {
      labels.push(`Status: ${displayChoice(filters.application_status)}`);
    }
    if (filters.work_arrangement) {
      labels.push(`Work arrangement: ${displayChoice(filters.work_arrangement)}`);
    }
    if (goneOnly) labels.push('Gone only');
    return labels;
  }, [sort, query, bestMatchMode, verdictFilter, remoteOnly, countryFilter, filters, goneOnly]);

  async function loadInitial() {
    setLoading(true);
    setError('');
    try {
      const [prof, sum, filt, src, status, uj, cov, cand] = await Promise.all([
        apiFetch('/api/profile/'),
        apiFetch('/api/stats/summary/'),
        apiFetch('/api/stats/filters/'),
        apiFetch('/api/sources/'),
        apiFetch('/api/sources/status/'),
        apiFetch('/api/user-jobs/'),
        apiFetch('/api/search/coverage/'),
        apiFetch('/api/employer-candidates/'),
      ]);
      setProfile(prof);
      setSummary(sum);
      setFilterOptions(filt);
      setSources(Array.isArray(src) ? src : src.results || []);
      setSourceStatus(status);
      setUserJobs(Array.isArray(uj) ? uj : uj.results || []);
      setCoverage(cov);
      setCandidates(Array.isArray(cand) ? cand : cand.results || []);
      if (hasPreferences(prof)) {
        setBestMatchMode(true);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadInitial();
  }, []);

  const buildParams = useCallback(() => {
    const params = {};
    if (sort === 'best') {
      if (query) params.q = query;
    } else {
      if (query) params.search = query;
    }
    MORE_FILTERS.forEach(({ key }) => {
      if (filters[key]) params[key] = filters[key];
    });
    if (filters.application_status) params.application_status = filters.application_status;
    if (filters.work_arrangement) params.work_arrangement = filters.work_arrangement;
    if (goneOnly) params.gone = 'true';
    if (remoteOnly) params.work_arrangement = 'remote';
    if (countryFilter) params.country = countryFilter;
    return params;
  }, [sort, query, filters, goneOnly, remoteOnly, countryFilter]);

  const loadPage = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = buildParams();
      const base = sort === 'best' ? '/api/search/' : '/api/jobs/';
      const qs = buildQuery(params);
      const url = qs ? `${base}?${qs}` : base;
      const data = await apiFetch(url);
      setJobs(data.results || []);
      setCount(data.count || 0);
      setNextUrl(data.next);
      setVerdictCounts(
        data.verdict_counts || { apply: 0, track: 0, ignore: 0, not_scored: 0 }
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [buildParams, sort]);

  useEffect(() => {
    loadPage();
  }, [loadPage]);

  async function loadMore() {
    if (!nextUrl) return;
    setLoading(true);
    setError('');
    try {
      const data = await apiFetch(nextUrl);
      setJobs((prev) => [...prev, ...(data.results || [])]);
      setNextUrl(data.next);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function handleFilterChange(key, value) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    if (key === 'application_status' && value) {
      setSavedFilter(false);
    }
  }

  function clearFilters() {
    setVerdictFilter('');
    setSavedFilter(false);
    setCountryFilter('');
    setRemoteOnly(false);
    setGoneOnly(false);
    setFilters({
      seniority: '',
      employment_type: '',
      work_arrangement: '',
      application_status: '',
      source_listing_status: '',
    });
    setQuery('');
    setBestMatchMode(false);
  }

  function handleTileClick(key) {
    if (key === 'saved') {
      setSavedFilter((prev) => !prev);
      setVerdictFilter('');
      setBestMatchMode(false);
      setFilters((prev) => ({ ...prev, application_status: '' }));
      return;
    }
    setSavedFilter(false);
    if (filters.application_status) {
      setFilters((prev) => ({ ...prev, application_status: '' }));
    }
    if (verdictFilter === key) {
      setVerdictFilter('');
    } else {
      setVerdictFilter(key);
      setBestMatchMode(false);
    }
  }

  function handleCountryClick(country) {
    if (country === 'Remote') {
      setRemoteOnly((prev) => !prev);
      return;
    }
    setCountryFilter((prev) => (prev === country ? '' : country));
  }

  function handleSortChange(newSort) {
    setSort(newSort);
    if (newSort !== 'best') {
      setBestMatchMode(false);
      setVerdictFilter('');
    }
  }

  async function updateUserJob(jobId, updates) {
    setError('');
    try {
      const existing = userJobsMap[jobId];
      let data;
      if (existing) {
        data = await apiFetch(`/api/user-jobs/${existing.id}/`, {
          method: 'PATCH',
          body: JSON.stringify(updates),
        });
      } else {
        data = await apiFetch('/api/user-jobs/', {
          method: 'POST',
          body: JSON.stringify({ job: jobId, ...updates }),
        });
      }
      setUserJobs((prev) => {
        const next = prev.filter((uj) => uj.id !== data.id);
        next.push(data);
        return next;
      });
    } catch (err) {
      setError(err.message);
    }
  }

  const handleSave = (jobId) => updateUserJob(jobId, { application_status: 'saved' });
  const handleNotInterested = (jobId) => updateUserJob(jobId, { application_status: 'closed' });

  const displayedJobs = useMemo(() => {
    let list = jobs;
    if (sort === 'best') {
      if (bestMatchMode) {
        list = list.filter((j) => j.verdict === 'apply' || j.verdict === 'track');
      } else if (verdictFilter) {
        list = list.filter((j) => j.verdict === verdictFilter);
      }
    }
    if (savedFilter) {
      list = list.filter((j) => userJobsMap[j.id]?.application_status === 'saved');
    }
    return list;
  }, [jobs, sort, bestMatchMode, verdictFilter, savedFilter, userJobsMap]);

  const totalJobs = summary?.total_jobs ?? count;
  const noProfile = profile && !hasPreferences(profile);

  if (!profile && loading) {
    return (
      <section aria-label="Roles" className="roles-screen">
        <div className="skeleton title-skeleton" />
        <div className="skeleton tiles-skeleton" />
        <div className="skeleton list-skeleton" />
      </section>
    );
  }

  return (
    <section aria-label="Roles" className="roles-screen">
      <div className="roles-header">
        <h2>Roles</h2>
        <div className="sort-toggle" role="group" aria-label="Sort">
          <button
            type="button"
            className={classNames(sort === 'best' && 'active')}
            onClick={() => handleSortChange('best')}
          >
            Best match
          </button>
          <button
            type="button"
            className={classNames(sort === 'newest' && 'active')}
            onClick={() => handleSortChange('newest')}
          >
            Newest
          </button>
        </div>
      </div>

      {error && <p className="error-message" role="alert">{error}</p>}

      <Onboarding
        profile={profile}
        candidates={candidates}
        sources={sources}
        jobs={jobs}
        totalJobs={totalJobs}
      />

      {noProfile && (
        <div className="info-message">
          Scores are not available yet. Add your preferences in Profile to see ranked roles.
        </div>
      )}

      <div className="summary-tiles">
        {VERDICT_TILES.map((tile) => {
          const active =
            (tile.kind === 'verdict' && verdictFilter === tile.key) ||
            (tile.kind === 'saved' && savedFilter);
          const countValue =
            tile.key === 'saved'
              ? savedCount
              : verdictCounts[tile.key] ?? 0;
          return (
            <button
              key={tile.key}
              type="button"
              className={classNames('tile', `tile-${tile.key}`, active && 'active')}
              onClick={() => handleTileClick(tile.key)}
              aria-pressed={active}
            >
              <span className="tile-count">{countValue}</span>
              <span className="tile-label">{tile.label}</span>
            </button>
          );
        })}
      </div>

      <form
        className="search-bar"
        onSubmit={(e) => {
          e.preventDefault();
          loadPage();
        }}
      >
        <input
          type="search"
          className="search-input"
          placeholder="Search roles, companies, descriptions…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" className="primary" disabled={loading}>
          Search
        </button>
      </form>

      <div className="filter-bar">
        <div className="filter-chips">
          {VERDICT_TILES.slice(0, 3).map((tile) => (
            <button
              key={tile.key}
              type="button"
              className={classNames('chip-button', verdictFilter === tile.key && 'active')}
              onClick={() => handleTileClick(tile.key)}
              disabled={sort !== 'best'}
              title={sort !== 'best' ? 'Verdict filters only work in Best match mode' : ''}
            >
              {tile.label}
            </button>
          ))}
          <button
            type="button"
            className={classNames('chip-button', remoteOnly && 'active')}
            onClick={() => setRemoteOnly((prev) => !prev)}
          >
            Remote
          </button>
          {countryOptions.slice(0, 12).map((country) => (
            <button
              key={country}
              type="button"
              className={classNames('chip-button', countryFilter === country && 'active')}
              onClick={() => handleCountryClick(country)}
            >
              {country}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={classNames('secondary-button', showMoreFilters && 'active')}
          onClick={() => setShowMoreFilters((prev) => !prev)}
        >
          {showMoreFilters ? 'Less filters' : 'More filters'}
        </button>
      </div>

      {showMoreFilters && (
        <div className="more-filters">
          {MORE_FILTERS.map(({ key, label }) => (
            <div key={key} className="filter-field">
              <label htmlFor={`filter-${key}`}>{label}</label>
              <select
                id={`filter-${key}`}
                value={filters[key]}
                onChange={(e) => handleFilterChange(key, e.target.value)}
              >
                <option value="">All</option>
                {Object.keys(filterOptions[key] || {}).map((choice) => (
                  <option key={choice} value={choice}>
                    {displayChoice(choice)} ({filterOptions[key][choice]})
                  </option>
                ))}
              </select>
            </div>
          ))}
          <div className="filter-field">
            <label htmlFor="filter-work-arrangement">Work arrangement</label>
            <select
              id="filter-work-arrangement"
              value={filters.work_arrangement}
              onChange={(e) => handleFilterChange('work_arrangement', e.target.value)}
            >
              <option value="">All</option>
              {Object.keys(filterOptions.work_arrangement || {}).map((choice) => (
                <option key={choice} value={choice}>
                  {displayChoice(choice)} ({filterOptions.work_arrangement[choice]})
                </option>
              ))}
            </select>
          </div>
          <div className="filter-field">
            <label htmlFor="filter-application-status">Your stage</label>
            <select
              id="filter-application-status"
              value={filters.application_status}
              onChange={(e) => handleFilterChange('application_status', e.target.value)}
            >
              <option value="">All</option>
              {Object.keys(filterOptions.application_status || {}).map((choice) => (
                <option key={choice} value={choice}>
                  {displayChoice(choice)} ({filterOptions.application_status[choice]})
                </option>
              ))}
            </select>
          </div>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={goneOnly}
              onChange={(e) => setGoneOnly(e.target.checked)}
            />
            Show only gone listings
          </label>
        </div>
      )}

      {activeFilterLabels.length > 0 && (
        <div className="active-filters">
          <span className="active-filters-label">Active filters:</span>
          <div className="filter-tags">
            {activeFilterLabels.map((label) => (
              <span key={label} className="filter-tag">{label}</span>
            ))}
          </div>
          <button type="button" className="text-button" onClick={clearFilters}>
            Clear all
          </button>
        </div>
      )}

      {sort !== 'best' && verdictFilter && (
        <p className="hint">Verdict filters are only available in Best match mode.</p>
      )}

      {jobs.length > 0 && jobs.some((j) => j.heuristic) && (
        <p className="info-message heuristic-note">
          Scores are a heuristic based on your profile. Review each role before deciding.
        </p>
      )}

      {loading && jobs.length === 0 ? (
        <>
          <div className="skeleton list-skeleton" />
          <div className="skeleton list-skeleton" />
          <div className="skeleton list-skeleton" />
        </>
      ) : (
        <>
          <div className="results-header">
            <p className="results-count">
              {bestMatchMode
                ? `Showing ${displayedJobs.length} best matches out of ${count} roles`
                : `${count} role${count === 1 ? '' : 's'} found`}
            </p>
            {bestMatchMode && (
              <button type="button" className="text-button" onClick={() => setBestMatchMode(false)}>
                Show all {totalJobs} roles
              </button>
            )}
          </div>

          {displayedJobs.length === 0 && !error && (
            <div className="empty-results">
              <p>
                {activeFilterLabels.length > 0
                  ? 'No roles match the current filters.'
                  : 'No roles found. Jobs will appear here after a scan imports listings.'}
              </p>
              {activeFilterLabels.length > 0 && (
                <button type="button" className="secondary" onClick={clearFilters}>
                  Clear filters to see all roles
                </button>
              )}
            </div>
          )}

          <div className="job-list">
            {displayedJobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                sourceMap={sourceMap}
                userJob={userJobsMap[job.id]}
                onSave={handleSave}
                onNotInterested={handleNotInterested}
              />
            ))}
          </div>

          <div className="pagination">
            <button
              type="button"
              className="secondary"
              disabled={loading || !nextUrl}
              onClick={() => nextUrl && loadMore()}
            >
              {loading ? 'Loading…' : 'Load more'}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

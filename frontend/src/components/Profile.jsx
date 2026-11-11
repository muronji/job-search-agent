import { useEffect, useState } from 'react';
import { apiFetch, clearToken } from '../api';

const DEFAULT_PARSED = {
  headline: '',
  summary: '',
  location: '',
  skills: [],
  experience: [],
  education: [],
  preferences: {
    job_titles: [],
    industries: [],
    locations: [],
    employment_types: [],
    work_arrangements: [],
    seniority_levels: [],
    min_salary: null,
  },
};

const DEFAULT_SCAN_PROFILE = {
  home_country: '',
  relocation_countries: [],
  remote_enabled: true,
  years_of_experience: null,
  access_table: [],
  gates: {
    exclude_countries: [],
    exclude_role_types: [],
    exclude_unpaid: true,
    max_years_required: null,
    clearance_phrases: [],
    missing_certifications: [],
  },
  thresholds: {
    apply: 75,
    track: 50,
    ignore: 30,
  },
  role_families: [],
};

function listToString(arr) {
  return (arr || []).join(', ');
}

function stringToList(value) {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function formatDate(value) {
  if (!value) return '';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export function Profile() {
  const [profile, setProfile] = useState(null);
  const [draft, setDraft] = useState(DEFAULT_PARSED);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [file, setFile] = useState(null);
  const [prefs, setPrefs] = useState(null);
  const [scanDraft, setScanDraft] = useState(DEFAULT_SCAN_PROFILE);

  useEffect(() => {
    loadProfile();
  }, []);

  async function loadProfile() {
    setLoading(true);
    setError('');
    try {
      const [data, prefData] = await Promise.all([
        apiFetch('/api/profile/'),
        apiFetch('/api/preferences/'),
      ]);
      setProfile(data);
      setDraft({ ...DEFAULT_PARSED, ...(data.parsed_profile || {}) });
      setPrefs(prefData);
      setScanDraft({ ...DEFAULT_SCAN_PROFILE, ...(prefData.scan_profile || {}) });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleConsentChange(enabled) {
    setError('');
    try {
      const data = await apiFetch('/api/profile/', {
        method: 'PATCH',
        body: JSON.stringify({ cv_parsing_consent: enabled }),
      });
      setProfile(data);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (!file) return;
    setError('');
    setSuccess('');
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('cv', file);
      const data = await apiFetch('/api/profile/upload/', {
        method: 'POST',
        body: formData,
      });
      setProfile(data);
      setFile(null);
      setSuccess('CV uploaded. Generating redacted preview…');
      await loadPreview();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadPreview() {
    setError('');
    try {
      const data = await apiFetch('/api/profile/preview/', {
        method: 'POST',
      });
      setPreview(data);
    } catch (err) {
      setError(err.message);
      setPreview(null);
    }
  }

  async function handleParse() {
    setError('');
    setSuccess('');
    setLoading(true);
    try {
      const data = await apiFetch('/api/profile/parse/', {
        method: 'POST',
      });
      setProfile(data);
      setDraft({ ...DEFAULT_PARSED, ...(data.parsed_profile || {}) });
      setSuccess('CV parsed successfully.');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    setError('');
    setSuccess('');
    setLoading(true);
    try {
      const data = await apiFetch('/api/profile/', {
        method: 'PATCH',
        body: JSON.stringify({ parsed_profile: draft }),
      });
      setProfile(data);
      setDraft({ ...DEFAULT_PARSED, ...(data.parsed_profile || {}) });
      setSuccess('Profile saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleDeleteCV() {
    if (!window.confirm('Delete your uploaded CV file? Your parsed profile will stay.')) {
      return;
    }
    setError('');
    setLoading(true);
    try {
      await apiFetch('/api/profile/cv/', { method: 'DELETE' });
      setPreview(null);
      await loadProfile();
      setSuccess('CV file deleted.');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleDeleteProfile() {
    if (!window.confirm('Delete your entire profile, CV, and parsed data? This cannot be undone.')) {
      return;
    }
    setError('');
    setLoading(true);
    try {
      await apiFetch('/api/profile/', { method: 'DELETE' });
      clearToken();
      window.location.reload();
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  }

  async function handleSaveScan() {
    setError('');
    setSuccess('');
    setLoading(true);
    try {
      const data = await apiFetch('/api/preferences/', {
        method: 'PATCH',
        body: JSON.stringify({ scan_profile: scanDraft }),
      });
      setPrefs(data);
      setScanDraft({ ...DEFAULT_SCAN_PROFILE, ...(data.scan_profile || {}) });
      setSuccess('Scan settings saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function updateScanField(field, value) {
    setScanDraft((prev) => ({ ...prev, [field]: value }));
  }

  function updateScanGate(field, value) {
    setScanDraft((prev) => ({
      ...prev,
      gates: { ...(prev.gates || {}), [field]: value },
    }));
  }

  function updateThreshold(field, value) {
    setScanDraft((prev) => ({
      ...prev,
      thresholds: { ...(prev.thresholds || {}), [field]: value },
    }));
  }

  function updateAccessRow(index, field, value) {
    setScanDraft((prev) => {
      const rows = [...(prev.access_table || [])];
      rows[index] = { ...rows[index], [field]: value };
      return { ...prev, access_table: rows };
    });
  }

  function removeAccessRow(index) {
    setScanDraft((prev) => {
      const rows = [...(prev.access_table || [])];
      rows.splice(index, 1);
      return { ...prev, access_table: rows };
    });
  }

  function addAccessRow() {
    setScanDraft((prev) => ({
      ...prev,
      access_table: [
        ...(prev.access_table || []),
        { country: '', score: 3, reason: '', last_reviewed: null },
      ],
    }));
  }

  function updateField(field, value) {
    setDraft((prev) => ({ ...prev, [field]: value }));
  }

  function updatePreference(key, value) {
    setDraft((prev) => ({
      ...prev,
      preferences: { ...(prev.preferences || {}), [key]: value },
    }));
  }

  function addExperience() {
    setDraft((prev) => ({
      ...prev,
      experience: [
        ...(prev.experience || []),
        { title: '', company: '', location: '', start_date: '', end_date: '', description: '' },
      ],
    }));
  }

  function updateExperience(index, field, value) {
    setDraft((prev) => {
      const list = [...(prev.experience || [])];
      list[index] = { ...list[index], [field]: value };
      return { ...prev, experience: list };
    });
  }

  function removeExperience(index) {
    setDraft((prev) => {
      const list = [...(prev.experience || [])];
      list.splice(index, 1);
      return { ...prev, experience: list };
    });
  }

  function addEducation() {
    setDraft((prev) => ({
      ...prev,
      education: [
        ...(prev.education || []),
        { institution: '', degree: '', field: '', start_date: '', end_date: '' },
      ],
    }));
  }

  function updateEducation(index, field, value) {
    setDraft((prev) => {
      const list = [...(prev.education || [])];
      list[index] = { ...list[index], [field]: value };
      return { ...prev, education: list };
    });
  }

  function removeEducation(index) {
    setDraft((prev) => {
      const list = [...(prev.education || [])];
      list.splice(index, 1);
      return { ...prev, education: list };
    });
  }

  if (!profile) {
    return (
      <section aria-label="Profile">
        {error && <p className="error-message" role="alert">{error}</p>}
        <p>Loading profile…</p>
      </section>
    );
  }

  const consent = Boolean(profile.cv_parsing_consent);
  const hasCV = Boolean(profile.cv);
  const canParse = consent && preview && hasCV;

  return (
    <section aria-label="Profile" className="profile-screen">
      <h2>Profile</h2>
      {error && <p className="error-message" role="alert">{error}</p>}
      {success && <p className="success-message" role="status">{success}</p>}

      <div className="consent-box">
        <label>
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => handleConsentChange(e.target.checked)}
          />
          <span>
            I consent to sending my redacted CV text to Kimi’s API for parsing.
          </span>
        </label>
        <p className="consent-notice">
          Before any text leaves this app, your name, email, phone number,
          address, and social handles are redacted. Only you can see the raw CV.
        </p>
      </div>

      <h3>CV upload</h3>
      <form onSubmit={handleUpload} className="button-row">
        <input
          type="file"
          accept=".txt,.pdf,.docx"
          onChange={(e) => setFile(e.target.files[0])}
        />
        <button type="submit" className="primary" disabled={!file || loading}>
          Upload CV
        </button>
        {hasCV && (
          <button type="button" className="secondary" onClick={loadPreview}>
            Refresh preview
          </button>
        )}
        {hasCV && (
          <button type="button" className="danger" onClick={handleDeleteCV}>
            Delete CV file
          </button>
        )}
      </form>

      {profile.cv && (
        <p className="file-name">
          Uploaded file: {String(profile.cv).split('/').pop()}
        </p>
      )}

      {preview && (
        <div className="redaction-preview">
          <h4>Redacted preview</h4>
          <pre>{preview.redacted_text}</pre>
          <div className="counts">
            {Object.entries(preview.counts || {}).map(([key, count]) => (
              <span key={key}>
                {key.replace('_count', '')}: {count}
              </span>
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        className="primary"
        onClick={handleParse}
        disabled={!canParse || loading}
      >
        Parse CV with Kimi
      </button>
      {!consent && <p className="hint">Enable consent and upload a CV to parse.</p>}

      <div className="scan-settings">
        <h3>Where would you work?</h3>
        <div className="form-group">
          <label>Home country</label>
          <input
            value={scanDraft.home_country || ''}
            onChange={(e) => updateScanField('home_country', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Relocation countries (comma-separated)</label>
          <input
            value={listToString(scanDraft.relocation_countries)}
            onChange={(e) => updateScanField('relocation_countries', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={scanDraft.remote_enabled}
              onChange={(e) => updateScanField('remote_enabled', e.target.checked)}
            />
            Include remote roles
          </label>
        </div>
        <div className="form-group">
          <label>Years of experience (override)</label>
          <input
            type="number"
            value={scanDraft.years_of_experience ?? ''}
            onChange={(e) =>
              updateScanField('years_of_experience', e.target.value === '' ? null : Number(e.target.value))
            }
          />
        </div>

        <h4>Access table</h4>
        <p className="hint">
          Scores are 1 (blocked) to 5 (ideal). The highest matching row is used.
          This is guidance, not legal advice — review it regularly.
        </p>
        {(scanDraft.access_table || []).map((row, i) => (
          <fieldset key={i} className="nested-form access-row">
            <div className="form-group">
              <label>Country / rule</label>
              <input
                value={row.country || ''}
                onChange={(e) => updateAccessRow(i, 'country', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Score</label>
              <input
                type="number"
                min="1"
                max="5"
                value={row.score || ''}
                onChange={(e) => updateAccessRow(i, 'score', Number(e.target.value))}
              />
            </div>
            <div className="form-group">
              <label>Reason</label>
              <input
                value={row.reason || ''}
                onChange={(e) => updateAccessRow(i, 'reason', e.target.value)}
              />
            </div>
            <button type="button" className="danger" onClick={() => removeAccessRow(i)}>
              Remove row
            </button>
          </fieldset>
        ))}
        <button type="button" className="secondary" onClick={addAccessRow}>
          Add access row
        </button>

        <h4>Gates</h4>
        <p className="hint">
          Gates cap a verdict at Track or force Ignore. They are generated from
          your profile and are editable.
        </p>
        <div className="form-group">
          <label>Excluded countries (comma-separated)</label>
          <input
            value={listToString(scanDraft.gates?.exclude_countries)}
            onChange={(e) => updateScanGate('exclude_countries', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Excluded role types (comma-separated)</label>
          <input
            value={listToString(scanDraft.gates?.exclude_role_types)}
            onChange={(e) => updateScanGate('exclude_role_types', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label className="checkbox-field">
            <input
              type="checkbox"
            checked={scanDraft.gates?.exclude_unpaid}
              onChange={(e) => updateScanGate('exclude_unpaid', e.target.checked)}
            />
            Exclude unpaid roles
          </label>
        </div>
        <div className="form-group">
          <label>Maximum years required you can accept</label>
          <input
            type="number"
            value={scanDraft.gates?.max_years_required ?? ''}
            onChange={(e) =>
              updateScanGate('max_years_required', e.target.value === '' ? null : Number(e.target.value))
            }
          />
        </div>
        <div className="form-group">
          <label>Clearance phrases that cap at Track (comma-separated)</label>
          <input
            value={listToString(scanDraft.gates?.clearance_phrases)}
            onChange={(e) => updateScanGate('clearance_phrases', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Certifications you do not have (comma-separated)</label>
          <input
            value={listToString(scanDraft.gates?.missing_certifications)}
            onChange={(e) => updateScanGate('missing_certifications', stringToList(e.target.value))}
          />
        </div>

        <h4>Verdict thresholds</h4>
        <div className="form-group">
          <label>Apply threshold (total score)</label>
          <input
            type="number"
            min="0"
            max="125"
            value={scanDraft.thresholds?.apply ?? 75}
            onChange={(e) => updateThreshold('apply', Number(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Track threshold (total score)</label>
          <input
            type="number"
            min="0"
            max="125"
            value={scanDraft.thresholds?.track ?? 50}
            onChange={(e) => updateThreshold('track', Number(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Ignore threshold (total score)</label>
          <input
            type="number"
            min="0"
            max="125"
            value={scanDraft.thresholds?.ignore ?? 30}
            onChange={(e) => updateThreshold('ignore', Number(e.target.value))}
          />
        </div>

        <div className="button-row">
          <button type="button" className="primary" onClick={handleSaveScan} disabled={loading}>
            Save scan settings
          </button>
        </div>
      </div>

      <div className="parsed-section">
        <h3>Parsed profile</h3>
        <div className="form-group">
          <label htmlFor="headline">Headline</label>
          <input
            id="headline"
            value={draft.headline || ''}
            onChange={(e) => updateField('headline', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label htmlFor="summary">Summary</label>
          <textarea
            id="summary"
            value={draft.summary || ''}
            onChange={(e) => updateField('summary', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label htmlFor="location">Location</label>
          <input
            id="location"
            value={draft.location || ''}
            onChange={(e) => updateField('location', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label htmlFor="skills">Skills (comma-separated)</label>
          <input
            id="skills"
            value={listToString(draft.skills)}
            onChange={(e) => updateField('skills', stringToList(e.target.value))}
          />
        </div>

        <h4>Experience</h4>
        {(draft.experience || []).map((exp, i) => (
          <fieldset key={i} className="nested-form">
            <div className="form-group">
              <label>Title</label>
              <input
                value={exp.title || ''}
                onChange={(e) => updateExperience(i, 'title', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Company</label>
              <input
                value={exp.company || ''}
                onChange={(e) => updateExperience(i, 'company', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Location</label>
              <input
                value={exp.location || ''}
                onChange={(e) => updateExperience(i, 'location', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Start date</label>
              <input
                value={exp.start_date || ''}
                onChange={(e) => updateExperience(i, 'start_date', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>End date</label>
              <input
                value={exp.end_date || ''}
                onChange={(e) => updateExperience(i, 'end_date', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Description</label>
              <textarea
                value={exp.description || ''}
                onChange={(e) => updateExperience(i, 'description', e.target.value)}
              />
            </div>
            <button type="button" className="danger" onClick={() => removeExperience(i)}>
              Remove experience
            </button>
          </fieldset>
        ))}
        <button type="button" className="secondary" onClick={addExperience}>
          Add experience
        </button>

        <h4>Education</h4>
        {(draft.education || []).map((edu, i) => (
          <fieldset key={i} className="nested-form">
            <div className="form-group">
              <label>Institution</label>
              <input
                value={edu.institution || ''}
                onChange={(e) => updateEducation(i, 'institution', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Degree</label>
              <input
                value={edu.degree || ''}
                onChange={(e) => updateEducation(i, 'degree', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Field</label>
              <input
                value={edu.field || ''}
                onChange={(e) => updateEducation(i, 'field', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Start date</label>
              <input
                value={edu.start_date || ''}
                onChange={(e) => updateEducation(i, 'start_date', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>End date</label>
              <input
                value={edu.end_date || ''}
                onChange={(e) => updateEducation(i, 'end_date', e.target.value)}
              />
            </div>
            <button type="button" className="danger" onClick={() => removeEducation(i)}>
              Remove education
            </button>
          </fieldset>
        ))}
        <button type="button" className="secondary" onClick={addEducation}>
          Add education
        </button>

        <h4>Preferences</h4>
        <div className="form-group">
          <label>Desired job titles (comma-separated)</label>
          <input
            value={listToString(draft.preferences?.job_titles)}
            onChange={(e) => updatePreference('job_titles', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Industries (comma-separated)</label>
          <input
            value={listToString(draft.preferences?.industries)}
            onChange={(e) => updatePreference('industries', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Locations (comma-separated)</label>
          <input
            value={listToString(draft.preferences?.locations)}
            onChange={(e) => updatePreference('locations', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Employment types (comma-separated)</label>
          <input
            value={listToString(draft.preferences?.employment_types)}
            onChange={(e) => updatePreference('employment_types', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Work arrangements (comma-separated)</label>
          <input
            value={listToString(draft.preferences?.work_arrangements)}
            onChange={(e) => updatePreference('work_arrangements', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Seniority levels (comma-separated)</label>
          <input
            value={listToString(draft.preferences?.seniority_levels)}
            onChange={(e) => updatePreference('seniority_levels', stringToList(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label>Minimum salary</label>
          <input
            type="number"
            value={draft.preferences?.min_salary ?? ''}
            onChange={(e) =>
              updatePreference('min_salary', e.target.value === '' ? null : Number(e.target.value))
            }
          />
        </div>
      </div>

      <div className="button-row">
        <button type="button" className="primary" onClick={handleSave} disabled={loading}>
          Save profile
        </button>
        <button type="button" className="danger" onClick={handleDeleteProfile} disabled={loading}>
          Delete my profile
        </button>
      </div>

      {profile.cv_parsing_consent_at && (
        <p className="consent-notice">
          Consent recorded: {formatDate(profile.cv_parsing_consent_at)}
        </p>
      )}
    </section>
  );
}

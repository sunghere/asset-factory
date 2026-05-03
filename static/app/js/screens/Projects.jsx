/* Projects — registry list + lifecycle (create / archive / unarchive / purge).
   Single-column row list (NOT card grid). Top-right "+ New Project" launches
   modal. Per-row hover surfaces ⋯ menu (archive / unarchive / purge / edit).
   Active task count drives a live pulse — purge modal also polls it.

   Response shape contract (AGENTS.md §6 — FE↔BE 계약):
     slug, display_name, description, created_at, archived_at, purge_status,
     asset_count, batch_count, candidate_count, active_task_count,
     last_active_at. server.py:_project_to_response 가 단일 source. */

const { useEffect, useMemo, useState, useCallback } = React;

const SLUG_RE = /^[a-z][a-z0-9-]{1,39}$/;
const RESERVED = new Set(['default', 'system', 'admin', 'api', 'null', 'undefined', '_', '__pycache__']);

function _isValidSlug(s) {
  if (!s) return false;
  if (s === 'default-project') return true;
  if (RESERVED.has(s)) return false;
  return SLUG_RE.test(s);
}

function _suggestSlug(value) {
  let s = (value || '').trim().toLowerCase();
  s = s.replace(/[^a-z0-9-]/g, '-').replace(/-+/g, '-').replace(/^-+|-+$/g, '');
  if (!s) return '';
  if (/^\d/.test(s)) s = 'p-' + s;
  return s.slice(0, 40);
}

function _relativeTime(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return '';
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  return `${d}d ago`;
}

function NewProjectModal({ open, onClose, onCreated }) {
  const [slug, setSlug] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [description, setDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState('');

  useEffect(() => {
    if (!open) {
      setSlug(''); setDisplayName(''); setDescription('');
      setSubmitting(false); setServerError('');
    }
  }, [open]);

  const slugError = useMemo(() => {
    if (!slug) return '';
    return _isValidSlug(slug)
      ? ''
      : `Lowercase, hyphens, 2-40 chars. Try: ${_suggestSlug(slug) || 'my-project'}`;
  }, [slug]);

  const canSubmit = slug && displayName && !slugError && !submitting;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setServerError('');
    try {
      const created = await window.api.createProject({
        slug, displayName, description: description || null,
      });
      onCreated(created);
      onClose();
    } catch (err) {
      const detail = err?.body?.detail;
      if (detail?.error === 'invalid_project_slug') {
        setServerError(`${detail.detail}${detail.suggestion ? ` (try ${detail.suggestion})` : ''}`);
      } else if (detail?.error === 'project_exists') {
        setServerError(`이미 등록된 slug 입니다: ${detail.slug}`);
      } else {
        setServerError(err?.message || '생성에 실패했습니다.');
      }
      setSubmitting(false);
    }
  }

  return (
    <window.Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      title="New Project"
      description="Slug 는 소문자/하이픈만 허용 (2-40자, 첫 글자는 알파벳)."
      size="md"
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose} disabled={submitting}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={handleSubmit} disabled={!canSubmit}>
            {submitting ? 'Creating…' : 'Create'}
          </button>
        </>
      }
    >
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>slug *</span>
          <input
            type="text"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="wooridul-factory"
            style={{ fontFamily: 'var(--font-mono)' }}
            aria-invalid={!!slugError}
            aria-describedby="slug-help"
          />
          {slugError && (
            <span id="slug-help" style={{ fontSize: 12, color: 'var(--accent-reject)' }}>{slugError}</span>
          )}
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>display name *</span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Wooridul Factory"
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>description</span>
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="(optional)"
          />
        </label>
        {serverError && (
          <div style={{ color: 'var(--accent-reject)', fontSize: 13 }}>{serverError}</div>
        )}
      </form>
    </window.Dialog>
  );
}

function PurgeModal({ project, onClose, onPurged }) {
  const [dryRun, setDryRun] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirmText, setConfirmText] = useState('');
  const [purging, setPurging] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    setError('');
    try {
      const r = await window.api.purgeProjectDryRun(project.slug);
      setDryRun(r);
    } catch (err) {
      setError(err?.message || 'dry-run 실패');
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    if (!project) return undefined;
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [project, refresh]);

  if (!project) return null;
  const blocking = dryRun?.blocking;
  const canConfirm = !purging && !blocking && confirmText === project.slug;

  async function handleConfirm() {
    setPurging(true);
    setError('');
    try {
      const r = await window.api.purgeProjectConfirm(project.slug);
      onPurged(project.slug, r);
      onClose();
    } catch (err) {
      setError(err?.body?.detail?.error || err?.message || 'purge 실패');
      setPurging(false);
    }
  }

  return (
    <window.Dialog
      open={true}
      onClose={purging ? undefined : onClose}
      title={<span style={{ color: 'var(--accent-reject)' }}>⚠ Permanently delete this project?</span>}
      description={project.slug}
      size="md"
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose} disabled={purging}>Cancel</button>
          <button
            type="button"
            className="btn"
            style={{
              background: canConfirm ? 'var(--accent-reject)' : 'var(--bg-elev-2)',
              color: canConfirm ? 'white' : 'var(--text-muted)',
              border: '1px solid var(--accent-reject)',
            }}
            onClick={handleConfirm}
            disabled={!canConfirm}
          >
            {purging ? 'Purging…' : 'Permanently delete'}
          </button>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {loading && <div style={{ color: 'var(--text-faint)' }}>Calculating…</div>}
        {!loading && dryRun && (
          <>
            <div style={{ fontSize: 13 }}>Will be removed:</div>
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-secondary)', fontSize: 13 }}>
              <li>{dryRun.will_delete.assets} assets</li>
              <li>{dryRun.will_delete.tasks} batches (with their tasks)</li>
              <li>{dryRun.will_delete.candidates} candidates</li>
              <li>
                {dryRun.will_delete.files} files
                {dryRun.will_delete.bytes
                  ? ` (${(dryRun.will_delete.bytes / (1024 * 1024)).toFixed(1)} MB)`
                  : ''}
              </li>
            </ul>
            {blocking && (
              <div
                style={{
                  background: 'var(--bg-elev-2)',
                  border: '1px solid var(--accent-approve)',
                  color: 'var(--accent-approve)',
                  padding: '8px 12px',
                  fontSize: 13,
                  borderRadius: 4,
                }}
              >
                ⚠ {blocking.active_task_count} task in flight. Wait or cancel before purge.
                <button
                  type="button"
                  className="btn ghost"
                  onClick={refresh}
                  style={{ marginLeft: 8, fontSize: 12 }}
                >Refresh status</button>
              </div>
            )}
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Type the project slug to confirm:
              </span>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={project.slug}
                style={{ fontFamily: 'var(--font-mono)' }}
                disabled={purging}
              />
            </label>
            {error && <div style={{ color: 'var(--accent-reject)', fontSize: 13 }}>{error}</div>}
          </>
        )}
      </div>
    </window.Dialog>
  );
}

function ProjectRow({ project, onArchive, onUnarchive, onPurge, onRetryPurge }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const archived = !!project.archived_at;
  const purging = project.purge_status === 'purging';
  return (
    <div
      role="listitem"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        padding: '12px 16px',
        background: 'var(--bg-elev-1)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 8,
        marginBottom: 8,
        opacity: archived ? 0.7 : 1,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-primary)',
          }}>{project.slug}</span>
          {archived && !purging && (
            <span className="chip chip-small" style={{ color: 'var(--text-muted)' }}>⊘ archived</span>
          )}
          {purging && (
            <span className="chip chip-small" style={{ color: 'var(--accent-approve)' }}>⏳ purging</span>
          )}
        </div>
        <div style={{ fontSize: 16, color: 'var(--text-primary)', marginTop: 2 }}>
          {project.display_name || project.slug}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
          {project.asset_count} assets
          <span style={{ color: 'var(--text-faint)' }}> · </span>
          {project.batch_count} batches
          <span style={{ color: 'var(--text-faint)' }}> · </span>
          {project.candidate_count} candidates
        </div>
        {project.active_task_count > 0 && (
          <div style={{ fontSize: 12, color: 'var(--accent-pick, var(--accent-approve))', marginTop: 4 }}>
            ⊙ {project.active_task_count} active task{project.active_task_count > 1 ? 's' : ''}
          </div>
        )}
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
        {_relativeTime(project.last_active_at || project.created_at)}
      </div>
      <div style={{ position: 'relative', minWidth: 44, minHeight: 44 }}>
        <button
          type="button"
          className="btn ghost"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label="actions"
          style={{ padding: '8px 12px' }}
        >⋯</button>
        {menuOpen && (
          <div
            role="menu"
            style={{
              position: 'absolute',
              right: 0,
              top: '100%',
              background: 'var(--bg-elev-2)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 6,
              padding: 4,
              zIndex: 10,
              minWidth: 140,
            }}
            onMouseLeave={() => setMenuOpen(false)}
          >
            {!archived && !purging && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => { setMenuOpen(false); onArchive(project); }}
                style={{ width: '100%', justifyContent: 'flex-start' }}
              >Archive</button>
            )}
            {archived && !purging && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => { setMenuOpen(false); onUnarchive(project); }}
                style={{ width: '100%', justifyContent: 'flex-start' }}
              >Unarchive</button>
            )}
            {archived && !purging && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => { setMenuOpen(false); onPurge(project); }}
                style={{ width: '100%', justifyContent: 'flex-start', color: 'var(--accent-reject)' }}
              >Purge…</button>
            )}
            {purging && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => { setMenuOpen(false); onRetryPurge(project); }}
                style={{ width: '100%', justifyContent: 'flex-start' }}
              >Retry purge</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Projects() {
  const [showArchived, setShowArchived] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [purgeTarget, setPurgeTarget] = useState(null);

  const projects = window.useAsync(
    () => window.api.listProjects({ includeArchived: true }),
    [],
  );
  const toasts = window.useToasts ? window.useToasts() : null;

  const items = projects.data?.items || [];
  const visible = useMemo(() => {
    if (showArchived) return items;
    return items.filter((p) => !p.archived_at && p.purge_status !== 'purging');
  }, [items, showArchived]);

  const counts = useMemo(() => ({
    active: items.filter((p) => !p.archived_at && p.purge_status !== 'purging').length,
    archived: items.filter((p) => p.archived_at && p.purge_status !== 'purging').length,
    purging: items.filter((p) => p.purge_status === 'purging').length,
  }), [items]);

  async function handleArchive(p) {
    try {
      await window.api.archiveProject(p.slug);
      toasts?.push({
        kind: 'info',
        message: `Archived ${p.slug}`,
        onUndo: async () => {
          try { await window.api.unarchiveProject(p.slug); } catch { /* noop */ }
          projects.reload();
        },
      });
      projects.reload();
    } catch (err) {
      toasts?.push({ kind: 'error', message: err?.message || 'archive 실패', ttl: 8000 });
    }
  }

  async function handleUnarchive(p) {
    try {
      await window.api.unarchiveProject(p.slug);
      toasts?.push({ kind: 'info', message: `Unarchived ${p.slug}` });
      projects.reload();
    } catch (err) {
      toasts?.push({ kind: 'error', message: err?.message || 'unarchive 실패', ttl: 8000 });
    }
  }

  async function handleRetryPurge(p) {
    try {
      const r = await window.api.purgeProjectRetry(p.slug);
      if (r.project_removed) {
        toasts?.push({ kind: 'info', message: `Purged ${p.slug}` });
      } else if (r.files_failed) {
        toasts?.push({ kind: 'warn', message: 'Files still pending — retry again later.' });
      }
      projects.reload();
    } catch (err) {
      toasts?.push({ kind: 'error', message: err?.message || 'retry 실패', ttl: 8000 });
    }
  }

  function handlePurged(slug, result) {
    if (result.project_removed) {
      toasts?.push({ kind: 'info', message: `Purged ${slug} — ${result.deleted.assets} assets removed.` });
    } else if (result.files_failed) {
      toasts?.push({ kind: 'warn', message: `DB cleared, ${result.deleted.files} files pending retry.`, ttl: 8000 });
    }
    projects.reload();
  }

  return (
    <div style={{ padding: '20px 28px', maxWidth: 960, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 18 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Projects</h1>
        <div style={{ flex: 1 }}/>
        <button
          type="button"
          className="btn"
          onClick={() => setNewOpen(true)}
          data-testid="new-project-btn"
          style={{
            background: 'var(--bg-elev-2)',
            border: '1px solid var(--border-strong)',
          }}
        >+ New Project</button>
      </div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 16, fontSize: 12,
        color: 'var(--text-muted)', marginBottom: 14,
      }}>
        <span>{counts.active} active</span>
        <span style={{ color: 'var(--text-faint)' }}>·</span>
        <span>{counts.archived} archived</span>
        {counts.purging > 0 && (
          <>
            <span style={{ color: 'var(--text-faint)' }}>·</span>
            <span style={{ color: 'var(--accent-approve)' }}>{counts.purging} purging</span>
          </>
        )}
        <div style={{ flex: 1 }}/>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          Show archived
        </label>
      </div>
      <div role="list">
        {projects.loading && <div style={{ color: 'var(--text-faint)' }}>Loading…</div>}
        {projects.error && (
          <div style={{ color: 'var(--accent-reject)' }}>{String(projects.error)}</div>
        )}
        {!projects.loading && visible.length === 0 && (
          <window.EmptyState
            title="프로젝트 없음"
            hint="+ New Project 로 시작하세요."
          />
        )}
        {visible.map((p) => (
          <ProjectRow
            key={p.slug}
            project={p}
            onArchive={handleArchive}
            onUnarchive={handleUnarchive}
            onPurge={(proj) => setPurgeTarget(proj)}
            onRetryPurge={handleRetryPurge}
          />
        ))}
      </div>
      <NewProjectModal
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={(p) => {
          toasts?.push({ kind: 'info', message: `Created ${p.slug}` });
          projects.reload();
        }}
      />
      {purgeTarget && (
        <PurgeModal
          project={purgeTarget}
          onClose={() => setPurgeTarget(null)}
          onPurged={handlePurged}
        />
      )}
    </div>
  );
}

window.Projects = Projects;

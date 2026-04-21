/* Batches — operational list of ALL design batches (not just pending).
   Contrasts with /queue which only shows what's left to cherry-pick.
   Columns surface the values we need for ops triage:
     - status pills (done / active / failed)
     - created-at for latency tracking
     - click-through to detail */

const { useMemo, useState } = React;

const SINCE_PRESETS = [
  { key: 'all', label: 'all time', hours: null },
  { key: '24h', label: '24h', hours: 24 },
  { key: '3d', label: '3d', hours: 72 },
  { key: '7d', label: '7d', hours: 168 },
];

function _sinceIso(hours) {
  if (!hours) return undefined;
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

function Batches() {
  const [statusFilter, setStatusFilter] = useState('all');
  const [projectFilter, setProjectFilter] = useState('all');
  const [sinceFilter, setSinceFilter] = useState('all');
  const [q, setQ] = useState('');
  const sinceIso = useMemo(() => {
    const preset = SINCE_PRESETS.find((x) => x.key === sinceFilter);
    return _sinceIso(preset?.hours ?? null);
  }, [sinceFilter]);
  const batches = window.useAsync(
    () => window.api.listBatches({ limit: 200, since: sinceIso }),
    [sinceIso],
  );

  window.useSSE((events) => {
    if (events.some((e) => [
      'batch_job_created', 'design_batch_created', 'task_done', 'task_error',
      'candidate_added', 'candidate_rejected', 'candidate_unrejected',
      'batch_retry_failed', 'batch_regenerate_failed_queued',
    ].includes(e.type))) batches.reload();
  });

  const items = batches.data?.items || [];

  const rows = useMemo(() => {
    let xs = items;
    if (statusFilter === 'processing') xs = xs.filter((b) => (b.active || 0) > 0);
    else if (statusFilter === 'failed') xs = xs.filter((b) => (b.failed || 0) > 0);
    else if (statusFilter === 'done') xs = xs.filter((b) => !(b.active > 0) && !(b.failed > 0));
    else if (statusFilter === 'queued') xs = xs.filter((b) => (b.total || 0) > 0 && (b.active || 0) === 0 && (b.failed || 0) === 0 && ((b.total || 0) - (b.active || 0) - (b.failed || 0)) === 0);
    if (projectFilter !== 'all') xs = xs.filter((b) => (b.project || 'default') === projectFilter);
    const needle = q.trim().toLowerCase();
    if (needle) {
      xs = xs.filter((b) =>
        (b.batch_id || '').toLowerCase().includes(needle)
        || (b.asset_key || '').toLowerCase().includes(needle)
        || (b.project || '').toLowerCase().includes(needle)
      );
    }
    return xs.slice().sort((a, b) => new Date(b.first_created_at) - new Date(a.first_created_at));
  }, [items, q, statusFilter, projectFilter]);

  const counts = useMemo(() => {
    const processing = items.filter((b) => (b.active || 0) > 0).length;
    const failed = items.filter((b) => (b.failed || 0) > 0).length;
    const done = items.filter((b) => !(b.active > 0) && !(b.failed > 0) && ((b.total || 0) - (b.active || 0) - (b.failed || 0)) > 0).length;
    const queued = items.filter((b) => (b.total || 0) > 0 && (b.active || 0) === 0 && (b.failed || 0) === 0 && ((b.total || 0) - (b.active || 0) - (b.failed || 0)) === 0).length;
    return { all: items.length, queued, processing, failed, done };
  }, [items]);

  const projects = useMemo(() => {
    const set = new Set(items.map((b) => b.project || 'default'));
    return ['all', ...Array.from(set).sort()];
  }, [items]);

  function statusPill(b) {
    if ((b.failed || 0) > 0) return <span className="pill pill-fail">failed {b.failed}</span>;
    if ((b.active || 0) > 0) return <span className="pill pill-warn">processing {b.active}</span>;
    const doneTasks = (b.total || 0) - (b.active || 0) - (b.failed || 0);
    if (doneTasks > 0) return <span className="pill pill-ok">done</span>;
    return <span className="pill">queued</span>;
  }

  function progressCell(b) {
    const total = Math.max(1, Number(b.total || 0));
    const failed = Number(b.failed || 0);
    const processing = Number(b.active || 0);
    const done = Math.max(0, total - failed - processing);
    const donePct = (done / total) * 100;
    const failedPct = (failed / total) * 100;
    const processingPct = (processing / total) * 100;
    return (
      <div style={{ minWidth: 120 }}>
        <div style={{ height: 7, borderRadius: 999, overflow: 'hidden', border: '1px solid var(--border-subtle)', display: 'flex' }}>
          <div style={{ width: `${donePct}%`, background: 'var(--accent-success)' }} />
          <div style={{ width: `${processingPct}%`, background: 'var(--accent-pick)' }} />
          <div style={{ width: `${failedPct}%`, background: 'var(--accent-reject)' }} />
        </div>
        <div style={{ marginTop: 4, fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-faint)' }}>
          {done}/{total} · p{processing} · f{failed}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="filter-row" style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        {[['all', 'all'], ['queued', 'queued'], ['processing', 'processing'], ['failed', 'failed'], ['done', 'done']].map(([k, label]) => (
          <span key={k} className={`pill ${statusFilter === k ? 'active' : ''}`} onClick={() => setStatusFilter(k)}>
            {label} <b style={{ marginLeft: 4 }}>{counts[k]}</b>
          </span>
        ))}
        <select className="input" value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)} style={{ width: 150 }}>
          {projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select className="input" value={sinceFilter} onChange={(e) => setSinceFilter(e.target.value)} style={{ width: 110 }}>
          {SINCE_PRESETS.map((p) => <option key={p.key} value={p.key}>since {p.label}</option>)}
        </select>
        <div style={{ flex: 1 }}/>
        <input
          className="input"
          placeholder="batch / asset / project 검색…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ width: 260 }}
        />
        <button className="btn" onClick={batches.reload} title="새로고침">↻</button>
        <a className="btn btn-primary" href="/app/batches/new" onClick={(e) => { e.preventDefault(); window.navigate('/batches/new'); }}>
          + 새 배치
        </a>
        <window.PageInfo
          title="batches"
          text="모든 design batch 의 운영 뷰 (Queue 는 pending 만 보여줌). 행 클릭으로 배치 상세."
        />
      </div>

      <window.ErrorPanel error={batches.error} onRetry={batches.reload}/>

      <div className="panel-card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 88 }}>status</th>
              <th>asset_key / batch_id</th>
              <th style={{ width: 110 }}>project</th>
              <th style={{ width: 150 }}>progress</th>
              <th style={{ width: 120 }}>candidates</th>
              <th style={{ width: 170 }}>first seen</th>
            </tr>
          </thead>
          <tbody>
            {batches.loading && !batches.data && (
              <tr><td colSpan={6} style={{ padding: 20 }}><window.Skeleton height={20}/></td></tr>
            )}
            {batches.data && rows.length === 0 && (
              <tr><td colSpan={6} style={{ padding: 30 }}>
                <window.EmptyState title="배치 없음" hint={q ? '검색어와 일치하는 배치가 없습니다.' : '필터를 완화하거나 새 배치를 등록해 보세요.'}/>
              </td></tr>
            )}
            {rows.map((b) => {
              return (
                <tr
                  key={b.batch_id}
                  className="row-link"
                  onClick={() => window.navigate(`/batches/${b.batch_id}`)}
                >
                  <td>{statusPill(b)}</td>
                  <td>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>{b.asset_key || '—'}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                      {b.batch_id}
                    </div>
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
                    {b.project || 'default'}
                  </td>
                  <td>{progressCell(b)}</td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                    {b.candidate_total || 0}
                    {b.rejected_count ? (
                      <span style={{ color: 'var(--accent-reject)', marginLeft: 6 }}>
                        −{b.rejected_count}
                      </span>
                    ) : null}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                    {(b.first_created_at || '').slice(0, 19).replace('T', ' ')}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

window.Batches = Batches;

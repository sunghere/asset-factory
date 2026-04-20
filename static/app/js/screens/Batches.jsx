/* Batches — operational list of ALL design batches (not just pending).
   Contrasts with /queue which only shows what's left to cherry-pick.
   Columns surface the values we need for ops triage:
     - status pills (done / active / failed)
     - created-at for latency tracking
     - click-through to detail */

const { useMemo, useState } = React;

function Batches() {
  const batches = window.useAsync(() => window.api.listBatches({ limit: 200 }), []);
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState('all');

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
    if (filter === 'active') xs = xs.filter((b) => (b.active || 0) > 0);
    else if (filter === 'failed') xs = xs.filter((b) => (b.failed || 0) > 0);
    else if (filter === 'done') xs = xs.filter((b) => !(b.active > 0) && !(b.failed > 0));
    const needle = q.trim().toLowerCase();
    if (needle) {
      xs = xs.filter((b) =>
        (b.batch_id || '').toLowerCase().includes(needle)
        || (b.asset_key || '').toLowerCase().includes(needle)
        || (b.project || '').toLowerCase().includes(needle)
      );
    }
    return xs.slice().sort((a, b) => new Date(b.first_created_at) - new Date(a.first_created_at));
  }, [items, q, filter]);

  const counts = useMemo(() => {
    const active = items.filter((b) => (b.active || 0) > 0).length;
    const failed = items.filter((b) => (b.failed || 0) > 0).length;
    const done = items.length - active - failed;
    return { all: items.length, active, failed, done };
  }, [items]);

  function statusPill(b) {
    if ((b.failed || 0) > 0) return <span className="pill pill-fail">fail {b.failed}</span>;
    if ((b.active || 0) > 0) return <span className="pill pill-warn">run {b.active}</span>;
    return <span className="pill pill-ok">done</span>;
  }

  return (
    <div>
      <div className="filter-row" style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        {[['all', 'all'], ['active', 'active'], ['failed', 'failed'], ['done', 'done']].map(([k, label]) => (
          <span key={k} className={`pill ${filter === k ? 'active' : ''}`} onClick={() => setFilter(k)}>
            {label} <b style={{ marginLeft: 4 }}>{counts[k]}</b>
          </span>
        ))}
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

      {batches.error && (
        <div className="error-banner" style={{ marginBottom: 12 }}>
          <span>⚠</span><span>{String(batches.error.message || batches.error)}</span>
        </div>
      )}

      <div className="panel-card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 88 }}>status</th>
              <th>asset_key / batch_id</th>
              <th style={{ width: 110 }}>project</th>
              <th style={{ width: 120 }}>tasks</th>
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
                <window.EmptyState title="배치 없음" hint={q ? '검색어와 일치하는 배치가 없습니다.' : '새 배치를 등록해 보세요.'}/>
              </td></tr>
            )}
            {rows.map((b) => {
              const tasksDone = (b.total || 0) - (b.active || 0) - (b.failed || 0);
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
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                    <span style={{ color: 'var(--accent-success)' }}>{tasksDone}</span>
                    <span style={{ color: 'var(--text-faint)' }}> / </span>
                    <span style={{ color: 'var(--text-muted)' }}>{b.total || 0}</span>
                  </td>
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

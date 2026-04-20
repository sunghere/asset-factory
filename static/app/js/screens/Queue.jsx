/* Queue — pending cherry-pick batches across all projects.
   Source: /api/cherry-pick/queue (already pre-filters approved batches).  */

const { useMemo, useState } = React;

function Queue() {
  const queue = window.useAsync(() => window.api.cherryPickQueue({ limit: 200 }), []);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');

  window.useSSE((batch) => {
    if (batch.some((e) => [
      'candidate_added', 'candidate_rejected', 'candidate_unrejected',
      'task_done', 'batch_job_created', 'design_batch_created',
      'asset_approved_from_candidate', 'asset_approve_undone',
      'batch_retry_failed', 'batch_regenerate_failed_queued',
      'validation_updated',
    ].includes(e.type))) queue.reload();
  });

  const projects = useMemo(() => {
    const items = queue.data?.items || [];
    return Array.from(new Set(items.map((b) => b.project || 'default')));
  }, [queue.data]);

  const rows = useMemo(() => {
    let items = queue.data?.items || [];
    if (filter !== 'all') items = items.filter((b) => (b.project || 'default') === filter);
    if (search.trim()) {
      const s = search.trim().toLowerCase();
      items = items.filter((b) =>
        (b.asset_key || '').toLowerCase().includes(s) || b.batch_id.toLowerCase().includes(s)
      );
    }
    return items.sort((a, b) => new Date(b.first_created_at) - new Date(a.first_created_at));
  }, [queue.data, filter, search]);

  return (
    <div>
      <div className="filter-row" style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className={`pill ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>
          all <b style={{ marginLeft: 4 }}>{queue.data?.items?.length ?? 0}</b>
        </span>
        {projects.map((p) => {
          const count = (queue.data?.items || []).filter((b) => (b.project || 'default') === p).length;
          return (
            <span key={p} className={`pill ${filter === p ? 'active' : ''}`} onClick={() => setFilter(p)}>
              {p} <b style={{ marginLeft: 4 }}>{count}</b>
            </span>
          );
        })}
        <div style={{ flex: 1 }}/>
        <input
          className="input"
          placeholder="batch_id / asset_key 검색…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 220 }}
        />
        <button className="btn" onClick={queue.reload} title="새로고침">↻</button>
        <window.PageInfo
          title="queue"
          text="pending cherry-pick batches. 행을 클릭하면 해당 배치의 체리픽 화면으로 이동합니다. Enter 는 Dashboard 에서 첫 대기 배치로 바로 진입."
        />
      </div>

      <window.ErrorPanel error={queue.error} onRetry={queue.reload}/>

      <div className="panel-card" style={{ padding: 0 }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 32 }}></th>
              <th>asset_key</th>
              <th style={{ width: 110 }}>project</th>
              <th style={{ width: 90 }}>남음</th>
              <th style={{ width: 90 }}>총</th>
              <th style={{ width: 220 }}>진행</th>
              <th style={{ width: 160 }}>최근 후보</th>
            </tr>
          </thead>
          <tbody>
            {queue.loading && !queue.data && (
              <tr><td colSpan={7} style={{ padding: 20 }}><window.Skeleton height={20}/></td></tr>
            )}
            {queue.data && rows.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 30 }}>
                <window.EmptyState glyph="∅" title="큐 비어있음" hint="필터/검색을 비우거나 새 batch 를 등록하세요."/>
              </td></tr>
            )}
            {rows.map((b) => {
              const done = (b.total || 0) - (b.remaining || 0);
              return (
                <tr
                  key={b.batch_id}
                  className="row-link"
                  onClick={() => window.navigate(`/cherry-pick/${b.batch_id}`)}
                >
                  <td style={{ color: 'var(--text-faint)' }}>◐</td>
                  <td>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>{b.asset_key}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                      {b.batch_id}
                    </div>
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
                    {b.project || 'default'}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-approve)', fontWeight: 600 }}>
                    {b.remaining}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{b.total}</td>
                  <td>
                    <window.SegProgress approved={done} rejected={0} total={b.total || 1}/>
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                    {(b.last_created_at || '').slice(0, 19).replace('T', ' ')}
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

window.Queue = Queue;

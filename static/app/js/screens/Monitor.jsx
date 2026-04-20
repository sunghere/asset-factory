/* Monitor — live SSE feed.

   Subscribes to /api/events via the buffered useSSE hook. Events render in a
   high-density log style; auto-scroll can be toggled (useful when
   investigating a specific event without losing it to the next batch).  */

const { useState, useRef, useCallback, useEffect } = React;

const KIND_COLOR = {
  candidate_rejected:    'var(--accent-warning)',
  candidate_unrejected:  'var(--text-muted)',
  asset_approved:        'var(--accent-approve)',
  asset_approve_undone:  'var(--accent-warning)',
  design_batch_created:  'var(--accent-pick)',
  batch_job_created:     'var(--accent-pick)',
  generation_completed:  'var(--accent-success)',
  generation_failed:     'var(--accent-reject)',
};

function Monitor() {
  const [events, setEvents] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState('');
  const listRef = useRef(null);
  const MAX = 1000;

  const onBatch = useCallback((batch) => {
    if (paused) return;
    setEvents((prev) => {
      const next = [...prev, ...batch];
      if (next.length > MAX) next.splice(0, next.length - MAX);
      return next;
    });
  }, [paused]);

  window.useSSE(onBatch, { active: true });

  useEffect(() => {
    if (!autoScroll || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [events, autoScroll]);

  const filtered = filter
    ? events.filter((e) => JSON.stringify(e).toLowerCase().includes(filter.toLowerCase()))
    : events;

  const counts = events.reduce((m, e) => { m[e.type] = (m[e.type] || 0) + 1; return m; }, {});

  return (
    <div>
      <window.PageToolbar
        left={
          <span className={`chip ${paused ? 'warn' : ''}`}>
            {paused ? '⏸ paused' : '● live'} · <b>{events.length}</b> events
          </span>
        }
        right={
          <>
            <button className="btn" onClick={() => setPaused((p) => !p)}>
              {paused ? '▶ resume' : '⏸ pause'}
            </button>
            <button className="btn" onClick={() => setAutoScroll((a) => !a)}>
              {autoScroll ? '⇣ auto-scroll on' : '· auto-scroll off'}
            </button>
            <button className="btn" onClick={() => setEvents([])}>clear</button>
          </>
        }
        info={{
          title: 'monitor',
          text: '/api/events SSE 스트림. useSSE 가 rAF로 배치 flush 해서 초당 수백 이벤트도 버벅임 없이 렌더. pause 는 UI 에만 영향 (서버 스트림은 계속).',
        }}
      />

      <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
        {Object.entries(counts).map(([k, v]) => (
          <span key={k} className="pill" onClick={() => setFilter(k)}>
            <span style={{ color: KIND_COLOR[k] || 'var(--text-secondary)' }}>{k}</span>
            <b style={{ marginLeft: 4 }}>{v}</b>
          </span>
        ))}
        {filter && (
          <span className="pill active" onClick={() => setFilter('')}>
            filter: {filter} ✕
          </span>
        )}
        <div style={{ flex: 1 }}/>
        <input
          className="input"
          placeholder="filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ width: 200 }}
        />
      </div>

      <div
        ref={listRef}
        className="monitor-log"
        style={{
          background: 'var(--bg-elev-1)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 8,
          padding: 10,
          height: 'calc(100vh - 280px)',
          overflowY: 'auto',
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
          lineHeight: 1.7,
        }}
      >
        {filtered.length === 0 && (
          <div style={{ color: 'var(--text-faint)', textAlign: 'center', padding: 40 }}>
            { paused ? '(paused)' : '(이벤트 대기 중…)'}
          </div>
        )}
        {filtered.map((e, i) => (
          <div key={i} style={{ display: 'flex', gap: 10, padding: '2px 4px' }}>
            <span style={{ color: 'var(--text-faint)' }}>
              {new Date(e._at || Date.now()).toLocaleTimeString('en-GB')}
            </span>
            <span style={{ color: KIND_COLOR[e.type] || 'var(--text-secondary)', minWidth: 180 }}>
              {e.type}
            </span>
            <span style={{ color: 'var(--text-muted)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {Object.entries(e)
                .filter(([k]) => k !== 'type' && k !== '_at')
                .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                .join(' · ')}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

window.Monitor = Monitor;

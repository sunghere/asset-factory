/* Queue — pending cherry-pick batches across all projects.
   Source: /api/cherry-pick/queue (already pre-filters approved batches).
   Full-spec rows: asset_key 큰 글씨 + chips (project / category / remaining) +
   progress bar + 작업하기 버튼 + ⋯ 메뉴, ↑↓ J/K 키보드 내비게이션,
   합계 footer ("N batches · M장 · 예상 X분"). */

const { useMemo, useState, useEffect, useRef } = React;

// 1장당 평균 픽 시간(초). 대시보드 직관 (한 장당 3~4초) 기반.
const _QUEUE_SEC_PER_CANDIDATE = 3;

function _relTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return `${Math.max(1, Math.floor(diff))}초 전`;
    if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
    return `${Math.floor(diff / 86400)}일 전`;
  } catch (_) { return '—'; }
}

function _formatEta(totalRemaining) {
  const sec = totalRemaining * _QUEUE_SEC_PER_CANDIDATE;
  if (sec < 60) return `${sec}초`;
  if (sec < 3600) return `${Math.round(sec / 60)}분`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m > 0 ? `${h}시 ${m}분` : `${h}시간`;
}

function Queue() {
  const queue = window.useAsync(() => window.api.cherryPickQueue({ limit: 200 }), []);
  const toasts = window.useToasts ? window.useToasts() : null;
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [cursor, setCursor] = useState(0);
  const [menuOpen, setMenuOpen] = useState(null);
  const bodyRef = useRef(null);

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

  useEffect(() => {
    if (cursor >= rows.length) setCursor(Math.max(0, rows.length - 1));
  }, [rows.length]);

  const totalBatches = rows.length;
  const totalRemaining = useMemo(
    () => rows.reduce((sum, b) => sum + Number(b.remaining || 0), 0),
    [rows],
  );

  window.useKeyboard({
    ArrowDown: (e) => { e.preventDefault?.(); setCursor((c) => Math.min(rows.length - 1, c + 1)); },
    ArrowUp: (e) => { e.preventDefault?.(); setCursor((c) => Math.max(0, c - 1)); },
    j: (e) => { e.preventDefault?.(); setCursor((c) => Math.min(rows.length - 1, c + 1)); },
    k: (e) => { e.preventDefault?.(); setCursor((c) => Math.max(0, c - 1)); },
    Enter: () => {
      const b = rows[cursor];
      if (b) window.navigate(`/cherry-pick/${b.batch_id}`);
    },
    Escape: () => { setMenuOpen(null); },
  }, [rows, cursor]);

  // 키보드로 cursor 가 이동하면 해당 행을 뷰포트로 스크롤.
  useEffect(() => {
    if (!bodyRef.current) return;
    const tr = bodyRef.current.querySelector(`tr[data-row="${cursor}"]`);
    if (tr && tr.scrollIntoView) tr.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [cursor]);

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
          text="pending cherry-pick batches. ↑↓ / J·K 로 행 이동, Enter 로 해당 batch 진입. 행 우측 '작업하기' 또는 ⋯ 메뉴로도 이동 가능."
        />
      </div>

      <window.ErrorPanel error={queue.error} onRetry={queue.reload}/>

      <div className="panel-card" style={{ padding: 0 }} onClick={() => setMenuOpen(null)}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 32 }}></th>
              <th>asset_key / chips</th>
              <th style={{ width: 220 }}>진행</th>
              <th style={{ width: 140 }}>최근 후보</th>
              <th style={{ width: 180 }}></th>
            </tr>
          </thead>
          <tbody ref={bodyRef}>
            {queue.loading && !queue.data && (
              <tr><td colSpan={5} style={{ padding: 20 }}><window.Skeleton height={20}/></td></tr>
            )}
            {queue.data && rows.length === 0 && (
              <tr><td colSpan={5} style={{ padding: 30 }}>
                <window.EmptyState glyph="∅" title="큐 비어있음" hint="필터/검색을 비우거나 새 batch 를 등록하세요."/>
              </td></tr>
            )}
            {rows.map((b, idx) => {
              const done = (b.total || 0) - (b.remaining || 0);
              const active = idx === cursor;
              const category = b.category || b.asset_category || null;
              return (
                <tr
                  key={b.batch_id}
                  data-row={idx}
                  className={`row-link${active ? ' row-active' : ''}`}
                  onClick={() => { setCursor(idx); window.navigate(`/cherry-pick/${b.batch_id}`); }}
                  style={active ? { outline: '1px solid var(--accent-pick)', outlineOffset: -1 } : undefined}
                >
                  <td style={{ color: active ? 'var(--accent-pick)' : 'var(--text-faint)', textAlign: 'center' }}>
                    {active ? '▶' : '◐'}
                  </td>
                  <td>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
                      {b.asset_key}
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      <span className="chip">{b.project || 'default'}</span>
                      {category && <span className="chip">{category}</span>}
                      <span className="chip" style={{ color: 'var(--accent-approve)' }}>
                        남음 <b style={{ marginLeft: 4 }}>{b.remaining}</b>
                      </span>
                      <span className="chip" style={{ color: 'var(--text-muted)' }}>
                        총 {b.total}
                      </span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                        {b.batch_id}
                      </span>
                    </div>
                  </td>
                  <td>
                    <window.SegProgress approved={done} rejected={0} total={b.total || 1}/>
                    <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
                      {done} / {b.total}
                    </div>
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                    {_relTime(b.last_created_at)}
                  </td>
                  <td style={{ textAlign: 'right', position: 'relative' }}>
                    <button
                      className="btn btn-primary"
                      onClick={(e) => {
                        e.stopPropagation();
                        setCursor(idx);
                        window.navigate(`/cherry-pick/${b.batch_id}`);
                      }}
                    >
                      작업하기
                    </button>
                    <button
                      className="btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpen(menuOpen === b.batch_id ? null : b.batch_id);
                      }}
                      title="더보기"
                      style={{ marginLeft: 4 }}
                      aria-haspopup="menu"
                      aria-expanded={menuOpen === b.batch_id}
                    >⋯</button>
                    {menuOpen === b.batch_id && (
                      <RowMenu
                        onClose={() => setMenuOpen(null)}
                        onOpen={() => window.navigate(`/cherry-pick/${b.batch_id}`)}
                        onDetail={() => window.navigate(`/batches/${b.batch_id}`)}
                        onCopyId={() => {
                          try {
                            navigator.clipboard.writeText(b.batch_id);
                            toasts?.push?.({ kind: 'info', message: 'batch_id 복사됨', ttl: 1500 });
                          } catch (_) { /* noop */ }
                        }}
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{
        marginTop: 10, padding: '10px 14px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)',
        border: '1px solid var(--border-subtle)', borderRadius: 4,
      }}>
        <span>
          합계 <b style={{ color: 'var(--text-primary)' }}>{totalBatches}</b> batches ·
          {' '}<b style={{ color: 'var(--accent-approve)' }}>{totalRemaining.toLocaleString()}</b>장 남음
        </span>
        <span>
          예상 소요 <b style={{ color: 'var(--text-primary)' }}>{totalRemaining > 0 ? _formatEta(totalRemaining) : '0분'}</b>
          <span style={{ marginLeft: 6, color: 'var(--text-faint)' }}>
            (≈{_QUEUE_SEC_PER_CANDIDATE}s/장)
          </span>
        </span>
      </div>
    </div>
  );
}

function RowMenu({ onClose, onOpen, onDetail, onCopyId }) {
  // Popover 스타일. 외부 클릭은 상위 <div> onClick 에서 setMenuOpen(null) 로 처리.
  const stop = (fn) => (e) => { e.stopPropagation(); onClose(); fn(); };
  return (
    <div
      role="menu"
      onClick={(e) => e.stopPropagation()}
      style={{
        position: 'absolute', right: 0, top: '100%', marginTop: 4,
        background: 'var(--bg-elev-1, #0f0f0f)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 4, minWidth: 160, padding: 4, zIndex: 30,
        boxShadow: '0 4px 12px rgba(0,0,0,0.35)',
      }}
    >
      <button className="btn" style={{ width: '100%', justifyContent: 'flex-start', textAlign: 'left' }} onClick={stop(onOpen)}>
        ▶ 체리픽으로 이동
      </button>
      <button className="btn" style={{ width: '100%', justifyContent: 'flex-start', textAlign: 'left' }} onClick={stop(onDetail)}>
        상세 보기
      </button>
      <button className="btn" style={{ width: '100%', justifyContent: 'flex-start', textAlign: 'left' }} onClick={stop(onCopyId)}>
        batch_id 복사
      </button>
    </div>
  );
}

window.Queue = Queue;

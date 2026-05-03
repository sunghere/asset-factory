/* Queue — pending cherry-pick batches across all projects.
   Source: /api/cherry-pick/queue (already pre-filters approved batches).
   Full-spec rows: asset_key 큰 글씨 + chips (project / category / remaining) +
   progress bar + 작업하기 버튼 + ⋯ 메뉴, ↑↓ J/K 키보드 내비게이션,
   합계 footer ("N batches · M장 · 예상 X분"). */

const { useMemo, useState, useEffect, useRef } = React;

// 스펙 기준 ETA: 남은 후보 1장당 약 1.5초.
const _QUEUE_SEC_PER_CANDIDATE = 1.5;
const SINCE_PRESETS = [
  { key: 'today', label: '오늘 00:00 (KST)' },
  { key: '24h', label: '최근 24시간' },
  { key: '7d', label: '최근 7일' },
];

function _queueSafeIsoFromMs(ms) {
  const d = new Date(ms);
  const ts = d.getTime();
  if (!Number.isFinite(ts)) return new Date().toISOString();
  return d.toISOString();
}

function _queueSinceIso(key) {
  try {
    const nowMs = Date.now();
    if (!Number.isFinite(nowMs)) return new Date().toISOString();
    if (key === '24h') return _queueSafeIsoFromMs(nowMs - 24 * 60 * 60 * 1000);
    if (key === '7d') return _queueSafeIsoFromMs(nowMs - 7 * 24 * 60 * 60 * 1000);

    // "오늘 00:00 (KST)"를 locale 문자열 파싱 없이 계산해 브라우저별 파싱 차이를 피한다.
    const kstOffsetMs = 9 * 60 * 60 * 1000;
    const kstNow = new Date(nowMs + kstOffsetMs);
    const kstMidnightUtcMs =
      Date.UTC(
        kstNow.getUTCFullYear(),
        kstNow.getUTCMonth(),
        kstNow.getUTCDate(),
        0, 0, 0, 0,
      ) - kstOffsetMs;
    return _queueSafeIsoFromMs(kstMidnightUtcMs);
  } catch (_) {
    return new Date().toISOString();
  }
}

function _relTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const ts = d.getTime();
  if (!Number.isFinite(ts)) return '—';
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return `${Math.max(1, Math.floor(diff))}초 전`;
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}

function _formatEta(totalRemaining) {
  const sec = totalRemaining * _QUEUE_SEC_PER_CANDIDATE;
  if (sec < 60) return `${Math.round(sec)}초`;
  if (sec < 3600) return `${Math.round(sec / 60)}분`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m > 0 ? `${h}시 ${m}분` : `${h}시간`;
}

function Queue() {
  const [sincePreset, setSincePreset] = useState('today');
  const [hideCompleted, setHideCompleted] = useState(true);
  const sinceIso = useMemo(() => _queueSinceIso(sincePreset), [sincePreset]);
  const queue = window.useAsync(() => window.api.cherryPickQueue({ since: sinceIso, limit: 200 }), [sinceIso]);
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
    if (hideCompleted) items = items.filter((b) => !b.approved);
    if (filter !== 'all') items = items.filter((b) => (b.project || 'default') === filter);
    if (search.trim()) {
      const s = search.trim().toLowerCase();
      items = items.filter((b) =>
        (b.asset_key || '').toLowerCase().includes(s) || b.batch_id.toLowerCase().includes(s)
      );
    }
    return items.sort((a, b) => new Date(b.first_created_at) - new Date(a.first_created_at));
  }, [queue.data, filter, search, hideCompleted]);

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
        <label style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>since</label>
        <select
          className="input"
          value={sincePreset}
          onChange={(e) => setSincePreset(e.target.value)}
          style={{ width: 170 }}
        >
          {SINCE_PRESETS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select>
        <label className="row" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginLeft: 4 }}>
          <input
            type="checkbox"
            checked={hideCompleted}
            onChange={(e) => setHideCompleted(e.target.checked)}
          />
          <span>숨김:완료된 batch</span>
        </label>
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
                <window.EmptyState
                  glyph="∅"
                  title="오늘 처리할 batch가 없습니다"
                  hint="새 배치를 등록하려면 아래 curl 예시를 실행하거나 수동 배치 화면으로 이동하세요."
                  action={(
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <button className="btn btn-primary" onClick={() => window.navigate('/batches/new')}>/batches/new 열기</button>
                      <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                        curl -X POST /api/batches -H 'Content-Type: application/json' -d ...
                      </code>
                    </div>
                  )}
                />
              </td></tr>
            )}
            {rows.map((b, idx) => {
              // task 도메인 (생성 진행) — list_recent_batches 가 채워주는 값.
              const taskTotal = Number(b.total || 0);
              const taskDone = Number(b.done || 0);
              const taskActive = Number(b.active || 0);
              const taskFailed = Number(b.failed || 0);
              // candidate 도메인 (cherry-pick 검토) — list_today_batches 가
              // count_pending_candidates 로 채움. b.remaining = rejected 안 된 후보.
              const reviewRemaining = Number(b.remaining || 0);
              const active = idx === cursor;
              const category = b.category || b.asset_category || null;
              const rejected = Number(b.rejected_count || 0);
              const picked = Number(b.picked_candidates || (b.approved ? 1 : 0));
              const statusIcon = b.approved ? '✓' : ((taskDone === 0 && rejected === 0) ? '★' : '◐');
              return (
                <tr
                  key={b.batch_id}
                  data-row={idx}
                  className={`row-link${active ? ' row-active' : ''}`}
                  onClick={() => { setCursor(idx); window.navigate(`/cherry-pick/${b.batch_id}`); }}
                  style={active ? { outline: '1px solid var(--accent-pick)', outlineOffset: -1 } : undefined}
                >
                  <td style={{ color: active ? 'var(--accent-pick)' : 'var(--text-faint)', textAlign: 'center' }}>
                    {active ? '▶' : statusIcon}
                  </td>
                  <td>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
                      {b.asset_key}
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      <span className="chip">{b.project || 'default'}</span>
                      {category && <span className="chip">{category}</span>}
                      <span className="chip" style={{ color: 'var(--accent-approve)' }}>
                        진행 <b style={{ marginLeft: 4 }}>{taskDone}/{taskTotal}</b>
                      </span>
                      {taskActive > 0 && (
                        <span className="chip" style={{ color: 'var(--text-muted)' }}>
                          처리중 {taskActive}
                        </span>
                      )}
                      {taskFailed > 0 && (
                        <span className="chip" style={{ color: 'var(--accent-reject)' }}>
                          실패 {taskFailed}
                        </span>
                      )}
                      <span className="chip" style={{ color: 'var(--accent-pick)' }}>
                        검토 {reviewRemaining}
                      </span>
                      <span className="chip" style={{ color: 'var(--text-muted)' }}>
                        rejected {rejected}
                      </span>
                      <span className="chip" style={{ color: 'var(--text-muted)' }}>
                        picked {picked}
                      </span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                        {b.batch_id}
                      </span>
                    </div>
                  </td>
                  <td>
                    {/* progress bar: task 생성 진행도 (done / total). 0/N 에서
                        시작해 N/N 으로 채워진다. failed 는 빨간 segment. */}
                    <window.SegProgress approved={taskDone} rejected={taskFailed} total={taskTotal || 1}/>
                    <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
                      {taskDone} / {taskTotal}
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
                        onRejectAll={async () => {
                          try {
                            const cand = await window.api.listBatchCandidates(b.batch_id);
                            const items = cand?.items || cand?.candidates || [];
                            let ok = 0;
                            for (const c of items) {
                              if (!c || c.status === 'rejected' || c.is_rejected) continue;
                              try {
                                await window.api.rejectCandidate(b.batch_id, c.id);
                                ok++;
                              } catch (_) { /* noop */ }
                            }
                            toasts?.push?.({
                              kind: 'info',
                              message: `이 batch 모두 reject 처리: ${ok}건`,
                              ttl: 2500,
                            });
                            queue.reload();
                          } catch (err) {
                            toasts?.push?.({
                              kind: 'error',
                              message: `일괄 reject 실패: ${err?.message || err}`,
                            });
                          }
                        }}
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

function RowMenu({ onClose, onOpen, onDetail, onRejectAll, onCopyId }) {
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
      <button className="btn" style={{ width: '100%', justifyContent: 'flex-start', textAlign: 'left' }} onClick={stop(onRejectAll)}>
        이 batch 모두 reject
      </button>
      <button className="btn" style={{ width: '100%', justifyContent: 'flex-start', textAlign: 'left' }} onClick={stop(onCopyId)}>
        batch_id 복사
      </button>
    </div>
  );
}

window.Queue = Queue;

/* CherryPick — keyboard-first pick screen.
   Keys:
     j / →  next        k / ←  prev
     1-5            star rate (local hint, not persisted yet)
     space          pick (POST approve-from-candidate, immediate undo toast)
     u              reject toggle (POST reject / unreject)
     shift+u        clear all rejects
     esc            undo last action (handled globally by ToastProvider)
     ?              help (placeholder) */

const { useMemo, useState, useCallback, useEffect } = React;

function CherryPick({ batchId }) {
  const candidates = window.useAsync(() => window.api.listBatchCandidates(batchId), [batchId]);
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(0);
  const [rates, setRates] = useState({}); // local star rating ([1..5]) per candidate id
  const toasts = window.useToasts();

  // Sync server-side data into local mutable view (so reject toggles feel instant).
  useEffect(() => {
    if (!candidates.data) return;
    setItems(candidates.data.candidates || []);
    setCursor(0);
  }, [candidates.data]);

  const meta = candidates.data?.batch || null;
  const remaining = useMemo(() => items.filter((c) => c.status !== 'rejected').length, [items]);
  const cur = items[cursor];

  const move = useCallback((dir) => {
    if (!items.length) return;
    setCursor((c) => Math.max(0, Math.min(items.length - 1, c + dir)));
  }, [items.length]);

  const setStatusLocal = useCallback((id, status) => {
    setItems((arr) => arr.map((c) => (c.id === id ? { ...c, status } : c)));
  }, []);

  const toggleReject = useCallback(async () => {
    if (!cur) return;
    const prev = cur.status;
    const next = prev === 'rejected' ? 'pending' : 'rejected';
    setStatusLocal(cur.id, next);
    try {
      if (next === 'rejected') await window.api.rejectCandidate(batchId, cur.id);
      else await window.api.unrejectCandidate(batchId, cur.id);
      const shortId = String(cur.id).slice(0, 8);
      toasts.push({
        kind: next === 'rejected' ? 'warning' : 'info',
        message: next === 'rejected' ? `반려됨 · ${shortId}` : `반려 해제 · ${shortId}`,
        onUndo: async () => {
          setStatusLocal(cur.id, prev);
          try {
            if (prev === 'rejected') await window.api.rejectCandidate(batchId, cur.id);
            else await window.api.unrejectCandidate(batchId, cur.id);
          } catch (err) { /* swallow */ }
        },
      });
    } catch (err) {
      setStatusLocal(cur.id, prev); // revert
      toasts.push({ kind: 'error', message: `반려 토글 실패: ${err.message || err}` });
    }
  }, [cur, batchId, setStatusLocal, toasts]);

  const pick = useCallback(async () => {
    if (!cur) return;
    const project = meta?.project || cur.project || 'default';
    const assetKey = meta?.asset_key || cur.asset_key;
    if (!assetKey) {
      toasts.push({ kind: 'error', message: 'asset_key 정보 없음 — 확정 불가' });
      return;
    }
    try {
      const resp = await window.api.approveFromCandidate({
        candidate_id: cur.id,
        project,
        asset_key: assetKey,
        prefer_format: 'webp',
      });
      const assetId = resp?.asset_id;
      toasts.push({
        kind: 'success',
        message: `채택됨 · ${assetKey}`,
        onUndo: assetId ? async () => {
          try { await window.api.undoApprove(assetId); }
          catch (err) { toasts.push({ kind: 'error', message: `실행취소 실패: ${err.message}` }); }
        } : undefined,
      });
      setStatusLocal(cur.id, 'approved');
      // Auto-advance to the next non-rejected candidate.
      setCursor((c) => Math.min(items.length - 1, c + 1));
    } catch (err) {
      toasts.push({ kind: 'error', message: `확정 실패: ${err.message || err}` });
    }
  }, [cur, items.length, meta, setStatusLocal, toasts]);

  const clearAllRejects = useCallback(async () => {
    const rejected = items.filter((c) => c.status === 'rejected');
    if (!rejected.length) return;
    setItems((arr) => arr.map((c) => (c.status === 'rejected' ? { ...c, status: 'pending' } : c)));
    await Promise.allSettled(rejected.map((c) => window.api.unrejectCandidate(batchId, c.id)));
    toasts.push({ kind: 'info', message: `${rejected.length}개 반려 해제` });
  }, [items, batchId, toasts]);

  window.useKeyboard({
    j: () => move(1),
    ArrowRight: () => move(1),
    k: () => move(-1),
    ArrowLeft: () => move(-1),
    ' ': pick,
    u: toggleReject,
    'shift+u': clearAllRejects,
    1: () => cur && setRates((r) => ({ ...r, [cur.id]: 1 })),
    2: () => cur && setRates((r) => ({ ...r, [cur.id]: 2 })),
    3: () => cur && setRates((r) => ({ ...r, [cur.id]: 3 })),
    4: () => cur && setRates((r) => ({ ...r, [cur.id]: 4 })),
    5: () => cur && setRates((r) => ({ ...r, [cur.id]: 5 })),
    Backspace: () => window.navigate('/queue'),
  }, [move, pick, toggleReject, clearAllRejects, cur]);

  const approvedCount = items.filter((c) => c.status === 'approved').length;
  const rejectedCount = items.filter((c) => c.status === 'rejected').length;

  return (
    <div className="cherry-screen">
      <div className="screen-header" style={{ marginBottom: 14 }}>
        <div>
          <div className="eyebrow">
            <window.Link to="/queue" style={{ color: 'var(--text-faint)' }}>../queue</window.Link>
            {' / '}
            <span style={{ color: 'var(--text-muted)' }}>{batchId.slice(0, 12)}…</span>
          </div>
          <h1>
            {meta?.asset_key || '…'} <span className="hint">({meta?.project || 'default'})</span>
          </h1>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          <span style={{ color: 'var(--accent-approve)' }}>✓ {approvedCount}</span>
          <span style={{ color: 'var(--text-muted)' }}>· ✕ {rejectedCount}</span>
          <span style={{ color: 'var(--text-faint)' }}>· {remaining}/{items.length} 남음</span>
        </div>
      </div>

      {candidates.error && <div className="error-banner" style={{ marginBottom: 12 }}>
        <span>⚠</span><span>{String(candidates.error.message || candidates.error)}</span>
      </div>}

      <div className="cherry-grid">
        {candidates.loading && Array.from({ length: 12 }).map((_, i) => (
          <window.Skeleton key={i} height={170}/>
        ))}
        {!candidates.loading && items.length === 0 && (
          <div style={{ gridColumn: '1 / -1' }}>
            <window.EmptyState glyph="∅" title="후보 없음" hint="이 배치에는 후보가 없습니다."/>
          </div>
        )}
        {items.map((c, i) => (
          <window.Thumb
            key={c.id}
            src={c.image_url}
            state={
              i === cursor ? 'cursor'
              : c.status === 'rejected' ? 'rejected'
              : c.status === 'approved' ? 'approved'
              : ''
            }
            badge={
              c.status === 'rejected' ? { kind: 'reject', label: '✕' }
              : c.status === 'approved' ? { kind: 'approve', label: '✓' }
              : rates[c.id] ? { kind: 'pick', label: '★'.repeat(rates[c.id]) }
              : undefined
            }
            warn={
              c.validation_status === 'fail'
                ? { kind: 'fail', label: '!', title: c.validation_message || 'validation=fail' }
                : undefined
            }
            caption={{
              left: `#${String(i + 1).padStart(2, '0')}`,
              right: String(c.id).slice(-6),
            }}
            onClick={() => setCursor(i)}
          />
        ))}
      </div>

      <div className="cherry-keystrip">
        <span><window.Kbd>j</window.Kbd>/<window.Kbd>k</window.Kbd> 이동</span>
        <span><window.Kbd>1</window.Kbd>–<window.Kbd>5</window.Kbd> 별점</span>
        <span><window.Kbd>space</window.Kbd> 채택</span>
        <span><window.Kbd>u</window.Kbd> 반려</span>
        <span><window.Kbd>shift+u</window.Kbd> 반려 해제</span>
        <span><window.Kbd>esc</window.Kbd> undo</span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-faint)' }}>
          backspace ← back to queue
        </span>
      </div>
    </div>
  );
}

window.CherryPick = CherryPick;

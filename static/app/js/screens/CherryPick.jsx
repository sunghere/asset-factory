/* CherryPick — keyboard-first 1 batch 검수 화면 (v0.2 spec §6.3 기반 재설계).

   좌측 그리드 + 우측 380px 사이드 패널 (Preview / Meta / Actions).
   전체 단축키는 ? 다이얼로그로도 확인 가능.

   단축키 (spec §6.3):
     j / →           다음 후보
     k / ←           이전 후보
     J / K           다음/이전 행 (열 수 기준)
     x               reject (5초 undo toast)
     v               비교 set 토글 (활성 후보)
     c               비교 Dialog 열기
     Enter           승인 → 다음 후보 (auto-advance 설정 따름)
     i               미리보기 zoom
     m               메타 패널 hide/show
     f               필터 포커스 (validation / lora)
     1-9             n번째 LoRA 필터 토글
     ?               도움말
     Esc             Dialog / zoom 닫기 / undo (ToastProvider 경유)
     Backspace       /queue 로 복귀
*/

const { useMemo: useMemoCP, useState: useStateCP, useCallback: useCallbackCP, useEffect: useEffectCP, useRef: useRefCP } = React;

// 로컬 설정값 (Settings 화면이 추후 이 키들을 관장).
function cpReadSetting(key, def) {
  try {
    const raw = window.localStorage?.getItem(key);
    if (raw == null) return def;
    return raw === 'true' ? true : raw === 'false' ? false : raw;
  } catch { return def; }
}

function CherryPick({ batchId }) {
  const candidates = window.useAsync(() => window.api.listBatchCandidates(batchId), [batchId]);
  const batchDetail = window.useAsync(() => window.api.getBatchDetail(batchId), [batchId]);

  const [items, setItems] = useStateCP([]);
  const [cursor, setCursor] = useStateCP(0);
  const [metaHidden, setMetaHidden] = useStateCP(false);
  const [zoomOpen, setZoomOpen] = useStateCP(false);
  const [compareOpen, setCompareOpen] = useStateCP(false);
  const [helpOpen, setHelpOpen] = useStateCP(false);
  const [hideRejected, setHideRejected] = useStateCP(false);
  const [loraFilters, setLoraFilters] = useStateCP(() => new Set()); // 활성 lora id 집합
  const [compareSet, setCompareSet] = useStateCP(() => new Set()); // candidate id 집합
  const [filterQuery, setFilterQuery] = useStateCP('');
  const filterInputRef = useRefCP(null);
  const gridRef = useRefCP(null);
  const toasts = window.useToasts();

  const autoAdvance = cpReadSetting('af_auto_advance', 'true') !== 'false';

  // server response 를 local mutable view 로 동기화.
  useEffectCP(() => {
    if (!candidates.data) return;
    const list = candidates.data.items || candidates.data.candidates || [];
    setItems(list);
    setCursor((c) => Math.min(c, Math.max(0, list.length - 1)));
  }, [candidates.data]);

  // SSE — candidate_added / rejected / unrejected 발생 시 목록을 부드럽게 재로드.
  window.useSSE((batch) => {
    if (!batch.some((e) => (
      (e.type === 'candidate_added' || e.type === 'candidate_rejected'
        || e.type === 'candidate_unrejected' || e.type === 'validation_updated')
      && e.batch_id === batchId
    ))) return;
    candidates.reload();
  });

  const meta = batchDetail.data || candidates.data?.batch || null;

  // LoRA 목록 (필터용) — items 에서 metadata_json.loras 꺼내 수집.
  const loraCatalog = useMemoCP(() => {
    const map = new Map();
    for (const c of items) {
      const meta = (() => {
        try { return typeof c.metadata_json === 'string' ? JSON.parse(c.metadata_json) : (c.metadata_json || {}); }
        catch { return {}; }
      })();
      for (const lora of (meta.loras || [])) {
        const id = lora.id || lora.name || String(lora);
        if (!map.has(id)) map.set(id, { id, weight: lora.weight });
      }
    }
    return Array.from(map.values()).slice(0, 9);
  }, [items]);

  // 보이는 후보 (hide rejected + lora filter + text filter 적용).
  const visibleItems = useMemoCP(() => {
    return items.filter((c) => {
      if (hideRejected && c.status === 'rejected') return false;
      if (loraFilters.size > 0) {
        let cMeta = {};
        try { cMeta = typeof c.metadata_json === 'string' ? JSON.parse(c.metadata_json) : (c.metadata_json || {}); } catch { /* */ }
        const cLoras = (cMeta.loras || []).map((l) => l.id || l.name || String(l));
        for (const need of loraFilters) {
          if (!cLoras.includes(need)) return false;
        }
      }
      if (filterQuery.trim()) {
        const q = filterQuery.trim().toLowerCase();
        const hay = `${c.generation_model || ''} ${c.generation_prompt || ''} ${c.validation_status || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [items, hideRejected, loraFilters, filterQuery]);

  // cursor는 visibleItems 기준. 원본 items 인덱스를 보존하지 않아도 OK.
  const cur = visibleItems[cursor] || null;
  const remaining = visibleItems.filter((c) => c.status !== 'rejected' && c.status !== 'approved').length;

  // 그리드 열 수 추정 (J/K 용) — CSS grid가 5열 기본이지만 화면 폭 따라 변함.
  // 실측값: grid element width / 셀 width (approx 200px 셀).
  const getColumns = useCallbackCP(() => {
    if (!gridRef.current) return 5;
    const w = gridRef.current.clientWidth;
    return Math.max(1, Math.floor(w / 200));
  }, []);

  const move = useCallbackCP((dir) => {
    if (!visibleItems.length) return;
    setCursor((c) => Math.max(0, Math.min(visibleItems.length - 1, c + dir)));
  }, [visibleItems.length]);

  const moveRow = useCallbackCP((dir) => {
    const cols = getColumns();
    move(dir * cols);
  }, [move, getColumns]);

  const setStatusLocal = useCallbackCP((id, status) => {
    setItems((arr) => arr.map((c) => (c.id === id ? { ...c, status } : c)));
  }, []);

  const reject = useCallbackCP(async () => {
    if (!cur || cur.status === 'rejected') return;
    const prev = cur.status;
    setStatusLocal(cur.id, 'rejected');
    try {
      await window.api.rejectCandidate(batchId, cur.id);
      const shortId = String(cur.id).slice(0, 8);
      toasts.push({
        kind: 'warning',
        message: `반려됨 · ${shortId}`,
        onUndo: async () => {
          setStatusLocal(cur.id, prev);
          try { await window.api.unrejectCandidate(batchId, cur.id); }
          catch { /* */ }
        },
      });
      if (autoAdvance) move(1);
    } catch (err) {
      setStatusLocal(cur.id, prev);
      toasts.push({ kind: 'error', message: `반려 실패: ${err.message || err}` });
    }
  }, [cur, batchId, setStatusLocal, toasts, autoAdvance, move]);

  const toggleCompare = useCallbackCP(() => {
    if (!cur) return;
    setCompareSet((s) => {
      const next = new Set(s);
      if (next.has(cur.id)) next.delete(cur.id);
      else next.add(cur.id);
      return next;
    });
  }, [cur]);

  const pick = useCallbackCP(async () => {
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
      if (autoAdvance) move(1);
    } catch (err) {
      toasts.push({ kind: 'error', message: `확정 실패: ${err.message || err}` });
    }
  }, [cur, meta, setStatusLocal, toasts, autoAdvance, move]);

  const toggleLoraFilterByIndex = useCallbackCP((idx) => {
    const lora = loraCatalog[idx - 1];
    if (!lora) return;
    setLoraFilters((s) => {
      const next = new Set(s);
      if (next.has(lora.id)) next.delete(lora.id);
      else next.add(lora.id);
      return next;
    });
  }, [loraCatalog]);

  // 단축키 바인딩.
  window.useKeyboard({
    j: () => move(1),
    ArrowRight: () => move(1),
    k: () => move(-1),
    ArrowLeft: () => move(-1),
    'shift+j': () => moveRow(1),
    'shift+k': () => moveRow(-1),
    Enter: pick,
    x: reject,
    v: toggleCompare,
    c: () => setCompareOpen(true),
    i: () => setZoomOpen((z) => !z),
    m: () => setMetaHidden((h) => !h),
    f: () => filterInputRef.current && filterInputRef.current.focus(),
    '?': () => setHelpOpen(true),
    '/': () => setHelpOpen(true),
    1: () => toggleLoraFilterByIndex(1),
    2: () => toggleLoraFilterByIndex(2),
    3: () => toggleLoraFilterByIndex(3),
    4: () => toggleLoraFilterByIndex(4),
    5: () => toggleLoraFilterByIndex(5),
    6: () => toggleLoraFilterByIndex(6),
    7: () => toggleLoraFilterByIndex(7),
    8: () => toggleLoraFilterByIndex(8),
    9: () => toggleLoraFilterByIndex(9),
    Backspace: () => window.navigate('/queue'),
  }, [move, moveRow, pick, reject, toggleCompare, toggleLoraFilterByIndex]);

  const approvedCount = items.filter((c) => c.status === 'approved').length;
  const rejectedCount = items.filter((c) => c.status === 'rejected').length;

  return (
    <div className="cherry-screen">
      {/* TopBar */}
      <div className="screen-header" style={{ marginBottom: 10, gap: 14, alignItems: 'center' }}>
        <div>
          <div className="eyebrow">
            <window.Link to="/queue" style={{ color: 'var(--text-faint)' }}>← /queue</window.Link>
            {' / '}
            <span style={{ color: 'var(--text-muted)' }}>{batchId.slice(0, 12)}…</span>
          </div>
          <h1 style={{ margin: '4px 0 0' }}>
            {meta?.asset_key || '…'} <span className="hint">({meta?.project || 'default'})</span>
          </h1>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label className="row" style={{ fontSize: 12 }}>
            <input
              type="checkbox"
              checked={hideRejected}
              onChange={(e) => setHideRejected(e.target.checked)}
            />
            <span>반려 숨김</span>
          </label>
          <input
            ref={filterInputRef}
            className="input"
            type="search"
            placeholder="filter (f)"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            style={{ width: 180, fontSize: 12 }}
          />
          <button
            className="btn ghost"
            onClick={() => setCompareOpen(true)}
            title="비교 Dialog (c)"
          >비교 <b>{compareSet.size}</b></button>
          <button
            className="btn ghost"
            onClick={() => setHelpOpen(true)}
            title="도움말 (?)"
            aria-label="도움말"
          >?</button>
          <span
            style={{
              fontFamily: 'var(--font-mono)', fontSize: 12, marginLeft: 8,
              color: 'var(--text-faint)',
            }}
            role="status"
            aria-label={`승인 ${approvedCount}, 반려 ${rejectedCount}, 남음 ${remaining}`}
          >
            <span style={{ color: 'var(--accent-approve)' }}>✓ {approvedCount}</span>
            {' · '}
            <span>✕ {rejectedCount}</span>
            {' · '}
            <span>{remaining}/{items.length} 남음</span>
          </span>
        </div>
      </div>

      {/* Progress (간단 버전 — 추후 SegProgress 컴포넌트가 생기면 교체) */}
      {items.length > 0 && (
        <div
          className="panel-card"
          role="progressbar"
          aria-valuenow={approvedCount}
          aria-valuemin={0}
          aria-valuemax={items.length}
          aria-label="승인 진행도"
          style={{
            padding: '8px 14px',
            marginBottom: 12,
            display: 'flex',
            gap: 12,
            alignItems: 'center',
            fontFamily: 'var(--font-mono)',
            fontSize: 12,
          }}
        >
          <span style={{ color: 'var(--text-muted)' }}>오늘 큐 › {meta?.asset_key || batchId.slice(0, 8)}</span>
          <div style={{ flex: 1, height: 6, background: 'var(--bg-recess)', borderRadius: 3, overflow: 'hidden' }}>
            <div
              style={{
                width: `${items.length ? (approvedCount / items.length) * 100 : 0}%`,
                height: '100%',
                background: 'var(--accent-approve)',
                transition: 'width 200ms',
              }}
            />
          </div>
          <span>{approvedCount} / {items.length}</span>
        </div>
      )}

      {candidates.error && <div className="error-banner" style={{ marginBottom: 12 }}>
        <span>⚠</span><span>{String(candidates.error.message || candidates.error)}</span>
      </div>}

      {/* Main: Grid + Side */}
      <div
        className="cherry-main"
        style={{
          display: 'grid',
          gridTemplateColumns: metaHidden ? '1fr' : 'minmax(0, 1fr) 380px',
          gap: 16,
          alignItems: 'flex-start',
        }}
      >
        <div
          ref={gridRef}
          className="cherry-grid"
          role="listbox"
          aria-label="후보 그리드"
          aria-activedescendant={cur ? `cand-${cur.id}` : undefined}
        >
          {candidates.loading && Array.from({ length: 12 }).map((_, i) => (
            <window.Skeleton key={i} height={170}/>
          ))}
          {!candidates.loading && visibleItems.length === 0 && (
            <div style={{ gridColumn: '1 / -1' }}>
              <window.EmptyState
                glyph="∅"
                title={items.length === 0 ? '후보 없음' : '필터 결과 없음'}
                hint={items.length === 0
                  ? '이 배치에는 후보가 없습니다.'
                  : '필터를 풀거나 반려 숨김을 해제해보세요.'}
              />
            </div>
          )}
          {visibleItems.map((c, i) => {
            const isCursor = i === cursor;
            const inCompare = compareSet.has(c.id);
            return (
              <window.Thumb
                key={c.id}
                id={`cand-${c.id}`}
                src={window.api.candidateImageUrl(c, 256)}
                alt={`candidate ${c.id} slot ${c.slot_index}`}
                state={
                  isCursor ? 'cursor'
                  : c.status === 'rejected' ? 'rejected'
                  : c.status === 'approved' ? 'approved'
                  : ''
                }
                badge={
                  c.status === 'rejected' ? { kind: 'reject', label: '✕' }
                  : c.status === 'approved' ? { kind: 'approve', label: '✓' }
                  : inCompare ? { kind: 'pick', label: 'v' }
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
                role="option"
                aria-selected={isCursor}
              />
            );
          })}
        </div>

        {!metaHidden && (
          <SidePanel
            cur={cur}
            meta={meta}
            compareSet={compareSet}
            onReject={reject}
            onCompareToggle={toggleCompare}
            onPick={pick}
            onZoom={() => setZoomOpen(true)}
          />
        )}
      </div>

      <div className="cherry-keystrip" role="note">
        <span><window.Kbd>j</window.Kbd>/<window.Kbd>k</window.Kbd> 이동</span>
        <span><window.Kbd>Shift</window.Kbd>+<window.Kbd>j</window.Kbd>/<window.Kbd>k</window.Kbd> 행</span>
        <span><window.Kbd>Enter</window.Kbd> 승인</span>
        <span><window.Kbd>x</window.Kbd> 반려</span>
        <span><window.Kbd>v</window.Kbd> 비교 토글</span>
        <span><window.Kbd>c</window.Kbd> 비교</span>
        <span><window.Kbd>i</window.Kbd> zoom</span>
        <span><window.Kbd>m</window.Kbd> 메타</span>
        <span><window.Kbd>f</window.Kbd> 필터</span>
        <span><window.Kbd>?</window.Kbd> 도움말</span>
      </div>

      {/* Zoom dialog — pixel-perfect preview */}
      <window.Dialog
        open={zoomOpen}
        onClose={() => setZoomOpen(false)}
        title={cur ? `zoom · #${cursor + 1} slot ${cur.slot_index}` : 'zoom'}
        size="lg"
      >
        {cur && (
          <div style={{ display: 'flex', justifyContent: 'center', background: '#000', padding: 20 }}>
            <img
              src={window.api.candidateImageUrl(cur, 512)}
              alt={`candidate ${cur.id} zoom`}
              style={{ imageRendering: 'pixelated', maxHeight: '70vh', maxWidth: '100%' }}
            />
          </div>
        )}
      </window.Dialog>

      {/* Compare dialog */}
      <CompareDialog
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        items={items.filter((c) => compareSet.has(c.id))}
        onClear={() => setCompareSet(new Set())}
      />

      {/* Help dialog */}
      <HelpDialog open={helpOpen} onClose={() => setHelpOpen(false)}/>
    </div>
  );
}

function SidePanel({ cur, meta, compareSet, onReject, onCompareToggle, onPick, onZoom }) {
  if (!cur) {
    return (
      <aside className="panel-card cherry-side" style={{ padding: 16, minHeight: 420 }}>
        <window.EmptyState glyph="✓" title="후보 없음" hint="cursor 를 그리드에서 선택하세요."/>
      </aside>
    );
  }

  let candMeta = {};
  try { candMeta = typeof cur.metadata_json === 'string' ? JSON.parse(cur.metadata_json) : (cur.metadata_json || {}); } catch { /* */ }
  const loras = candMeta.loras || [];

  return (
    <aside
      className="panel-card cherry-side"
      style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}
    >
      {/* Preview */}
      <div
        style={{
          width: '100%',
          aspectRatio: '1 / 1',
          background: '#0a0c10',
          border: '1px solid var(--line)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'zoom-in',
          overflow: 'hidden',
        }}
        onClick={onZoom}
        role="button"
        tabIndex={0}
        aria-label="확대 보기 (i)"
      >
        <img
          src={window.api.candidateImageUrl(cur, 384)}
          alt={`candidate ${cur.id} preview`}
          style={{ maxWidth: '100%', maxHeight: '100%', imageRendering: 'pixelated' }}
        />
      </div>

      {/* Meta list */}
      <dl
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr',
          rowGap: 4,
          columnGap: 10,
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
          margin: 0,
        }}
      >
        <dt style={{ color: 'var(--text-faint)' }}>asset_key</dt>
        <dd style={{ margin: 0 }}>{meta?.asset_key || cur.asset_key || '—'}</dd>
        <dt style={{ color: 'var(--text-faint)' }}>candidate</dt>
        <dd style={{ margin: 0 }}>#{cur.id} · slot {cur.slot_index}</dd>
        <dt style={{ color: 'var(--text-faint)' }}>seed</dt>
        <dd style={{ margin: 0 }}>{cur.generation_seed ?? '—'}</dd>
        <dt style={{ color: 'var(--text-faint)' }}>model</dt>
        <dd style={{ margin: 0, wordBreak: 'break-all' }}>{cur.generation_model || '—'}</dd>
        <dt style={{ color: 'var(--text-faint)' }}>lora</dt>
        <dd style={{ margin: 0 }}>
          {loras.length === 0 ? '—' : loras.map((l, i) => (
            <span key={i} style={{ marginRight: 6 }}>
              {l.id || l.name || String(l)}
              {l.weight != null && <span style={{ color: 'var(--text-faint)' }}>@{l.weight}</span>}
            </span>
          ))}
        </dd>
        <dt style={{ color: 'var(--text-faint)' }}>validation</dt>
        <dd style={{ margin: 0 }}>
          <span style={{ color: cur.validation_status === 'pass' ? 'var(--accent-approve)' : 'var(--accent-reject)' }}>
            {cur.validation_status || '—'}
          </span>
          {cur.color_count != null && <span style={{ color: 'var(--text-faint)', marginLeft: 6 }}>· {cur.color_count}c</span>}
        </dd>
        {cur.validation_message && (
          <>
            <dt style={{ color: 'var(--text-faint)' }}>message</dt>
            <dd style={{ margin: 0, color: 'var(--text-muted)' }}>{cur.validation_message}</dd>
          </>
        )}
      </dl>

      {cur.generation_prompt && (
        <details style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>prompt</summary>
          <pre style={{
            whiteSpace: 'pre-wrap', marginTop: 6, padding: 8,
            background: 'var(--bg-recess)', borderRadius: 4, overflow: 'auto', maxHeight: 140,
          }}>{cur.generation_prompt}</pre>
        </details>
      )}

      {cur.metadata_json && (
        <details style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>metadata_json</summary>
          <pre style={{
            whiteSpace: 'pre-wrap', marginTop: 6, padding: 8,
            background: 'var(--bg-recess)', borderRadius: 4, overflow: 'auto', maxHeight: 200,
          }}>{typeof cur.metadata_json === 'string' ? cur.metadata_json : JSON.stringify(cur.metadata_json, null, 2)}</pre>
        </details>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="btn"
          onClick={onReject}
          disabled={cur.status === 'rejected'}
        >✕ reject <span className="hint">(x)</span></button>
        <button
          type="button"
          className="btn ghost"
          onClick={onCompareToggle}
          aria-pressed={compareSet.has(cur.id)}
        >{compareSet.has(cur.id) ? 'v 비교 해제' : 'v 비교 추가'}</button>
        <button
          type="button"
          className="btn primary"
          onClick={onPick}
          disabled={cur.status === 'approved'}
          style={{ marginLeft: 'auto' }}
        >Enter ✓ 승인</button>
      </div>
    </aside>
  );
}

function CompareDialog({ open, onClose, items, onClear }) {
  return (
    <window.Dialog
      open={open}
      onClose={onClose}
      title={`비교 (${items.length})`}
      description={items.length === 0 ? '비교 set 이 비어있습니다. 그리드에서 v 를 눌러 후보를 추가하세요.' : '좌→우 동일 배율로 픽셀 정렬 보기.'}
      size="lg"
      footer={<>
        <button className="btn ghost" onClick={onClear} disabled={items.length === 0}>비우기</button>
        <button className="btn" onClick={onClose}>닫기</button>
      </>}
    >
      {items.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${Math.min(items.length, 4)}, minmax(160px, 1fr))`,
            gap: 10,
          }}
        >
          {items.map((c) => (
            <div key={c.id} className="panel-card" style={{ padding: 8 }}>
              <div style={{
                aspectRatio: '1 / 1', background: '#000',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <img
                  src={window.api.candidateImageUrl(c, 384)}
                  alt={`candidate ${c.id}`}
                  style={{ maxWidth: '100%', maxHeight: '100%', imageRendering: 'pixelated' }}
                />
              </div>
              <div style={{ marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                #{c.id} · seed {c.generation_seed ?? '—'}
                <div style={{ color: 'var(--text-faint)' }}>{c.generation_model || '—'}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </window.Dialog>
  );
}

function HelpDialog({ open, onClose }) {
  const rows = [
    ['j / →', '다음 후보'],
    ['k / ←', '이전 후보'],
    ['Shift + j / k', '한 행 아래/위로'],
    ['Enter', '승인 → 다음 후보'],
    ['x', '반려 (5초 undo toast)'],
    ['v', '비교 set 토글'],
    ['c', '비교 Dialog 열기'],
    ['i', '미리보기 zoom'],
    ['m', '메타 패널 숨김/표시'],
    ['f', '필터 input 포커스'],
    ['1 – 9', 'n번째 LoRA 필터 토글'],
    ['?', '이 도움말'],
    ['Esc', 'Dialog 닫기 / undo'],
    ['Backspace', '/queue 로 복귀'],
  ];
  return (
    <window.Dialog
      open={open}
      onClose={onClose}
      title="CherryPick 단축키"
      description="키보드에서 손이 떠나지 않도록 설계되었습니다."
      size="md"
      footer={<button className="btn primary" onClick={onClose}>확인</button>}
    >
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
        <tbody>
          {rows.map(([key, desc]) => (
            <tr key={key} style={{ borderTop: '1px solid var(--line-subtle)' }}>
              <td style={{ padding: '6px 0', width: 160, color: 'var(--text-muted)' }}>{key}</td>
              <td style={{ padding: '6px 0' }}>{desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </window.Dialog>
  );
}

window.CherryPick = CherryPick;

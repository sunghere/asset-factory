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

// 로컬 설정값 (Settings 화면이 이 키들을 관장).
function cpReadSetting(key, def) {
  try {
    const raw = window.localStorage?.getItem(key);
    if (raw == null) return def;
    return raw;
  } catch { return def; }
}

// 'on'/'off' (신규) 혹은 'true'/'false' (legacy) 어느 쪽이 저장돼 있어도
// 토글 상태를 안정적으로 읽는다.
function cpReadToggle(key, defaultOn) {
  const raw = cpReadSetting(key, defaultOn ? 'on' : 'off');
  if (raw === 'on' || raw === 'true') return true;
  if (raw === 'off' || raw === 'false') return false;
  return defaultOn;
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
  const [modelFilter, setModelFilter] = useStateCP('all');
  const [visibleLimit, setVisibleLimit] = useStateCP(60); // 1차 렌더 상한 — IO 로 확장.
  const [assetKeyDraft, setAssetKeyDraft] = useStateCP('');
  const [nextBatchCountdown, setNextBatchCountdown] = useStateCP(null); // {batchId, sec}
  const filterInputRef = useRefCP(null);
  const gridRef = useRefCP(null);
  const sentinelRef = useRefCP(null);
  const toasts = window.useToasts();
  const track = window.useAnalytics('cherrypick');
  const sessionStartRef = useRefCP(null);
  const sessionCountsRef = useRefCP({ approved: 0, rejected: 0 });

  // Settings 에서 저장한 'on'/'off' (또는 legacy 'true'/'false') 어느 쪽이든 해석.
  // af_auto_advance 기본값 on: Enter/x 후 자동으로 다음 후보로 이동.
  // af_keymap 기본값 on: j/k/Enter/x/... 전역 키맵 활성화.
  const autoAdvance = cpReadToggle('af_auto_advance', true);
  const keymapEnabled = cpReadToggle('af_keymap', true);

  // server response 를 local mutable view 로 동기화.
  useEffectCP(() => {
    if (!candidates.data) return;
    const list = candidates.data.items || candidates.data.candidates || [];
    setItems(list);
    setCursor((c) => Math.min(c, Math.max(0, list.length - 1)));
  }, [candidates.data]);

  useEffectCP(() => {
    const key = (batchDetail.data?.asset_key || candidates.data?.batch?.asset_key || '').trim();
    setAssetKeyDraft(key);
  }, [batchId, batchDetail.data?.asset_key, candidates.data?.batch?.asset_key]);

  // Cherry-pick session bracket — opt-in analytics only.
  // Emits:
  //   session.open  {batchId, total}          on first candidates load
  //   session.close {batchId, approved, rejected, duration_ms} on unmount
  useEffectCP(() => {
    if (!candidates.data) return undefined;
    if (sessionStartRef.current != null) return undefined;
    sessionStartRef.current = Date.now();
    sessionCountsRef.current = { approved: 0, rejected: 0 };
    track('session.open', { batchId, total: (candidates.data.items || []).length });
    return () => {
      // React StrictMode runs cleanup twice in dev; ensure we only fire once.
      if (sessionStartRef.current == null) return;
      const duration_ms = Date.now() - sessionStartRef.current;
      track('session.close', {
        batchId,
        approved: sessionCountsRef.current.approved,
        rejected: sessionCountsRef.current.rejected,
        duration_ms,
      });
      sessionStartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates.data, batchId]);

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

  const modelCatalog = useMemoCP(() => {
    const set = new Set();
    for (const c of items) {
      if (c.generation_model) set.add(c.generation_model);
    }
    return Array.from(set);
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
      if (modelFilter !== 'all' && c.generation_model !== modelFilter) return false;
      return true;
    });
  }, [items, hideRejected, loraFilters, filterQuery, modelFilter]);

  // cursor는 visibleItems 기준. 원본 items 인덱스를 보존하지 않아도 OK.
  const cur = visibleItems[cursor] || null;
  const remaining = visibleItems.filter((c) => c.status !== 'rejected' && c.status !== 'approved').length;

  // 필터가 바뀌면 visible limit 리셋. 그래야 필터 결과가 60 이하일 때 전체 노출.
  useEffectCP(() => { setVisibleLimit(60); }, [hideRejected, filterQuery, loraFilters, modelFilter]);

  // cursor 가 현재 렌더된 범위 너머로 이동하면 limit 확장.
  useEffectCP(() => {
    if (cursor >= visibleLimit - 2) {
      setVisibleLimit((n) => Math.min(visibleItems.length, n + 60));
    }
  }, [cursor, visibleLimit, visibleItems.length]);

  // sentinel 기반 IntersectionObserver — 마지막 행이 보이면 60개 추가 렌더.
  useEffectCP(() => {
    if (!sentinelRef.current) return undefined;
    if (visibleLimit >= visibleItems.length) return undefined;
    const obs = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setVisibleLimit((n) => Math.min(visibleItems.length, n + 60));
      }
    }, { rootMargin: '200px' });
    obs.observe(sentinelRef.current);
    return () => obs.disconnect();
  }, [visibleLimit, visibleItems.length]);

  // cursor ±2 prefetch — 다음/이전 후보 풀사이즈 이미지 미리 로드.
  useEffectCP(() => {
    if (!cur) return;
    const prefetchIdxs = [cursor - 2, cursor - 1, cursor + 1, cursor + 2];
    for (const i of prefetchIdxs) {
      const c = visibleItems[i];
      if (!c) continue;
      const url = window.api.candidateImageUrl(c, 384);
      if (!url) continue;
      const img = new Image();
      img.decoding = 'async';
      img.src = url;
    }
  }, [cursor, cur, visibleItems]);

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
      sessionCountsRef.current.rejected += 1;
      track('pick', { verdict: 'reject', id: cur.id, cursor });
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
  }, [cur, batchId, setStatusLocal, toasts, autoAdvance, move, track, cursor]);

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
    const assetKey = (assetKeyDraft || meta?.asset_key || cur.asset_key || '').trim();
    if (!assetKey) {
      toasts.push({ kind: 'error', message: 'asset_key 정보 없음 — 확정 불가' });
      return;
    }
    const pendingAfterThisPick = items.filter((c) =>
      c.id !== cur.id && c.status !== 'rejected' && c.status !== 'approved'
    ).length;
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
      sessionCountsRef.current.approved += 1;
      track('pick', { verdict: 'approve', id: cur.id, cursor, asset_key: assetKey });
      if (autoAdvance) move(1);
      if (pendingAfterThisPick === 0) {
        try {
          const q = await window.api.cherryPickQueue({ limit: 200 });
          const next = (q?.items || []).find((b) => !b.approved && b.batch_id !== batchId && Number(b.remaining || 0) > 0);
          if (next?.batch_id) setNextBatchCountdown({ batchId: next.batch_id, sec: 3 });
        } catch (_) {
          // next batch lookup 실패는 무시
        }
      }
    } catch (err) {
      toasts.push({ kind: 'error', message: `확정 실패: ${err.message || err}` });
    }
  }, [cur, meta, assetKeyDraft, items, setStatusLocal, toasts, autoAdvance, move, track, cursor, batchId]);

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

  // 단축키 바인딩. keymapEnabled=false 여도 '?' 는 항상 살려둬서 도움말로
  // 끈 상태임을 사용자에게 알릴 수 있게 한다.
  const keymap = keymapEnabled
    ? {
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
      }
    : {
        '?': () => setHelpOpen(true),
        '/': () => setHelpOpen(true),
      };
  window.useKeyboard(keymap, [keymapEnabled, move, moveRow, pick, reject, toggleCompare, toggleLoraFilterByIndex]);

  const approvedCount = items.filter((c) => c.status === 'approved').length;
  const rejectedCount = items.filter((c) => c.status === 'rejected').length;
  const remainingCount = Math.max(items.length - approvedCount - rejectedCount, 0);

  useEffectCP(() => {
    if (!nextBatchCountdown) return undefined;
    const id = setInterval(() => {
      setNextBatchCountdown((prev) => {
        if (!prev) return null;
        if (prev.sec <= 1) {
          window.navigate(`/cherry-pick/${prev.batchId}`);
          return null;
        }
        return { ...prev, sec: prev.sec - 1 };
      });
    }, 1000);
    return () => clearInterval(id);
  }, [nextBatchCountdown]);

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
            오늘 큐 › {meta?.asset_key || '…'} <span className="hint">({meta?.project || 'default'})</span>
          </h1>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn" onClick={() => candidates.reload()} title="reload">↻ reload</button>
          <button className="btn" onClick={() => window.navigate('/queue')} title="queue">batch 목록</button>
          <BatchOps batchId={batchId} meta={meta} onChanged={() => candidates.reload()}/>
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
          <select
            className="input"
            value={modelFilter}
            onChange={(e) => setModelFilter(e.target.value)}
            style={{ width: 180, fontSize: 12 }}
            title="model filter"
          >
            <option value="all">model: all</option>
            {modelCatalog.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <select
            className="input"
            value="__none__"
            onChange={(e) => {
              const id = e.target.value;
              if (id !== '__none__') {
                setLoraFilters((s) => {
                  const next = new Set(s);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                });
              }
              e.target.value = '__none__';
            }}
            style={{ width: 170, fontSize: 12 }}
            title="lora filter"
          >
            <option value="__none__">lora toggle…</option>
            {loraCatalog.map((l) => <option key={l.id} value={l.id}>{l.id}</option>)}
          </select>
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
            <span>{remainingCount}/{items.length} 남음</span>
          </span>
        </div>
      </div>

      {nextBatchCountdown && (
        <div className="panel-card" style={{ marginBottom: 10, padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            배치 완료. {nextBatchCountdown.sec}초 후 다음 배치로 이동합니다.
          </span>
          <button className="btn" style={{ marginLeft: 'auto' }} onClick={() => setNextBatchCountdown(null)}>취소</button>
          <button className="btn btn-primary" onClick={() => window.navigate(`/cherry-pick/${nextBatchCountdown.batchId}`)}>지금 이동</button>
        </div>
      )}

      {/* Progress */}
      {items.length > 0 && (
        <div
          className="panel-card"
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
          <div style={{ flex: 1 }}>
            <window.SegProgress approved={approvedCount} rejected={rejectedCount} total={items.length}/>
          </div>
          <span>✓ {approvedCount} · ✕ {rejectedCount} · 남음 {remainingCount}</span>
        </div>
      )}

      <window.ErrorPanel error={candidates.error} onRetry={candidates.reload}/>

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
                title={items.length === 0 ? '후보 없음' : (hideRejected ? '모두 rejected' : '필터 결과 없음')}
                hint={items.length === 0
                  ? '이 배치에는 후보가 없습니다.'
                  : (hideRejected ? '모두 rejected 입니다. [x]를 풀고 다시 보세요.' : '필터를 풀거나 반려 숨김을 해제해보세요.')}
                action={hideRejected && items.length > 0
                  ? <button className="btn btn-primary" onClick={() => setHideRejected(false)}>[x] 풀고 다시 보기</button>
                  : undefined}
              />
            </div>
          )}
          {visibleItems.slice(0, visibleLimit).map((c, i) => {
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
          {visibleLimit < visibleItems.length && (
            <div
              ref={sentinelRef}
              aria-hidden="true"
              style={{ gridColumn: '1 / -1', height: 20, color: 'var(--text-faint)', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11 }}
            >
              … {visibleItems.length - visibleLimit} more
            </div>
          )}
        </div>

        {!metaHidden && (
          <SidePanel
            cur={cur}
            meta={meta}
            assetKeyDraft={assetKeyDraft}
            onAssetKeyChange={setAssetKeyDraft}
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
      <HelpDialog
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
        keymapEnabled={keymapEnabled}
        autoAdvance={autoAdvance}
      />
    </div>
  );
}

function SidePanel({ cur, meta, assetKeyDraft, onAssetKeyChange, compareSet, onReject, onCompareToggle, onPick, onZoom }) {
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
        <dd style={{ margin: 0 }}>
          <input
            className="input"
            value={assetKeyDraft}
            onChange={(e) => onAssetKeyChange(e.target.value)}
            placeholder={meta?.asset_key || cur.asset_key || 'asset_key'}
            style={{ width: '100%', fontSize: 12 }}
          />
          <div style={{ fontSize: 10, color: 'var(--text-faint)', marginTop: 2 }}>현재 배치 승인 요청에 사용</div>
        </dd>
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
          className="btn btn-primary"
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

function HelpDialog({ open, onClose, keymapEnabled = true, autoAdvance = true }) {
  const enterDesc = autoAdvance ? '승인 → 다음 후보' : '승인 (자동 이동 off — 현재 위치 유지)';
  const rejectDesc = autoAdvance ? '반려 → 다음 후보 · 5초 undo toast' : '반려 · 5초 undo toast (자동 이동 off)';
  const rows = [
    ['j / →', '다음 후보'],
    ['k / ←', '이전 후보'],
    ['Shift + j / k', '한 행 아래/위로'],
    ['Enter', enterDesc],
    ['x', rejectDesc],
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
      footer={<button className="btn btn-primary" onClick={onClose}>확인</button>}
    >
      {!keymapEnabled && (
        <div
          role="note"
          style={{
            marginBottom: 12,
            padding: '8px 10px',
            border: '1px solid var(--accent-reject, #c56)',
            borderRadius: 6,
            background: 'rgba(200, 80, 100, 0.08)',
            fontSize: 12,
          }}
        >
          키맵이 <b>꺼져 있습니다</b> (af_keymap = off). <code>?</code> 와 <code>/</code>만
          동작합니다. <a href="/app/settings" onClick={(e) => { e.preventDefault(); onClose?.(); window.navigate('/settings'); }}>
            /settings
          </a> 에서 다시 켜세요.
        </div>
      )}
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
      <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-faint)' }}>
        현재 설정 · auto-advance: <b>{autoAdvance ? 'on' : 'off'}</b> · keymap: <b>{keymapEnabled ? 'on' : 'off'}</b>
        {' · '}
        <a href="/app/settings" onClick={(e) => { e.preventDefault(); onClose?.(); window.navigate('/settings'); }}>
          /settings 에서 변경
        </a>
      </div>
    </window.Dialog>
  );
}

/* BatchOps — cancel / delete 버튼 + 2-step 확인 다이얼로그.
   "취소": queued/processing → cancelled (history 보존).
   "삭제": task + candidate row + 디스크 파일 모두 제거.
     active 가 남아 있으면 409 (force 옵션 제공). */
function BatchOps({ batchId, meta, onChanged }) {
  const toasts = window.useToasts();
  const [confirm, setConfirm] = useStateCP(null); // 'cancel' | 'delete' | null
  const [busy, setBusy] = useStateCP(false);

  const totalActive = Number(meta?.active || 0);

  async function doCancel() {
    setBusy(true);
    try {
      const r = await window.api.cancelBatch(batchId);
      const n = (r.cancelled_queued || 0) + (r.cancelled_processing || 0);
      toasts.push({
        kind: 'success',
        message: n > 0
          ? `취소 완료 — ${n} task (queued ${r.cancelled_queued} / processing ${r.cancelled_processing})`
          : '취소할 task 가 없었습니다 (이미 모두 종료됨).',
        ttl: 5000,
      });
      onChanged?.();
    } catch (e) {
      toasts.push({ kind: 'error', message: '취소 실패: ' + (e.message || e), ttl: 6000 });
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  async function doDelete(force = false) {
    setBusy(true);
    try {
      const r = await window.api.deleteBatch(batchId, { force });
      toasts.push({
        kind: 'success',
        message: `삭제 완료 — task ${r.deleted_tasks}, candidate ${r.deleted_candidates}, 파일 ${r.unlinked_files}`,
        ttl: 6000,
      });
      window.navigate('/queue');
    } catch (e) {
      // 409 → active 가 남음. 사용자에게 cancel-then-delete 흐름 유도.
      if (e?.status === 409) {
        const detail = e.body?.detail || {};
        const active = detail.active ?? '?';
        const ok = window.confirm(
          `${active} 개의 task 가 아직 처리 중입니다. 먼저 '취소' 후 다시 시도하세요.\n` +
          `그래도 강제 삭제할까요? (race window 동안 워커가 결과를 마저 저장할 수 있음)`
        );
        if (ok) {
          await doDelete(true);
          return;
        }
        toasts.push({ kind: 'info', message: '삭제 취소됨.' });
      } else {
        toasts.push({ kind: 'error', message: '삭제 실패: ' + (e.message || e), ttl: 6000 });
      }
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  return (
    <>
      <button
        className="btn"
        disabled={busy}
        onClick={() => setConfirm('cancel')}
        title="진행중 task 를 cancelled 로 마킹 (디스크/DB 보존)"
      >취소</button>
      <button
        className="btn"
        disabled={busy}
        onClick={() => setConfirm('delete')}
        title="batch 의 task + candidate + 파일을 영구 삭제"
        style={{ color: 'var(--accent-reject)' }}
      >삭제</button>

      {confirm === 'cancel' && (
        <ConfirmDialog
          title="batch 취소?"
          body={
            totalActive > 0
              ? `${totalActive} 개의 진행중 task 를 cancelled 로 마킹합니다. 이미 done/failed 인 task 와 그동안 만들어진 candidate 는 그대로 보존됩니다.`
              : '진행중 task 가 없습니다. 그래도 cancel 마킹을 시도할까요?'
          }
          confirmLabel="취소 실행"
          danger={false}
          onConfirm={doCancel}
          onClose={() => setConfirm(null)}
        />
      )}
      {confirm === 'delete' && (
        <ConfirmDialog
          title="batch 영구 삭제?"
          body={
            <>
              이 batch 의 <b>모든 task / candidate row + 디스크 이미지</b> 를 삭제합니다.
              {' '}되돌릴 수 없습니다.
              {totalActive > 0 && (
                <div style={{ marginTop: 8, color: 'var(--accent-warning, var(--accent-pick))' }}>
                  진행중 task {totalActive} 개 — 먼저 '취소' 를 실행한 뒤 삭제하는 것을 권장합니다.
                </div>
              )}
            </>
          }
          confirmLabel={`삭제 (${meta?.asset_key || batchId.slice(0, 12)})`}
          danger={true}
          onConfirm={() => doDelete(false)}
          onClose={() => setConfirm(null)}
        />
      )}
    </>
  );
}

function ConfirmDialog({ title, body, confirmLabel, danger, onConfirm, onClose }) {
  return (
    <window.Dialog onClose={onClose} title={title}>
      <div style={{ marginBottom: 14, lineHeight: 1.5 }}>{body}</div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button className="btn" onClick={onClose}>닫기</button>
        <button
          className={`btn ${danger ? '' : 'btn-primary'}`}
          style={danger ? { background: 'var(--accent-reject)', color: '#fff', borderColor: 'var(--accent-reject)' } : undefined}
          onClick={onConfirm}
        >{confirmLabel}</button>
      </div>
    </window.Dialog>
  );
}

window.CherryPick = CherryPick;

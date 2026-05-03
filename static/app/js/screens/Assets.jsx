/* Assets — gallery of the canonical picks (one row per asset_key).
     - Left: filter sidebar (project / status / validation / category),
       driven by /api/assets/summary so counts are always truthful.
     - Right: responsive image grid with status dot + validation badge.
    - URL 쿼리 직렬화: 멀티 필터/정렬/검색/표시량을 ?p=&s=&v=&c=&q=&sort=&dir=&limit=
      로 보존하여 링크 공유 가능.
     - 정렬: created / updated / asset_key / color_count (클라이언트 정렬).
     - Bulk actions: 선택 시 '선택 승인 / 선택 거부 / 선택 재검증 / 선택 재생성'.
       검증 fail 만 모드에서는 기존 '재검증 / 실패분 재생성' 일괄 API 노출.
     - shift+↑↓ 다중 선택, shift/⌘+click 토글, plain click 은 상세 진입.
   Per-asset 액션 (validate / regenerate / approve toggle) 은 AssetDetail.jsx. */

const { useState, useMemo, useEffect, useCallback, useRef } = React;

const STATUS_OPTIONS = [
  { key: 'pending',  label: 'pending' },
  { key: 'approved', label: 'approved' },
  { key: 'rejected', label: 'rejected' },
];
const VALIDATION_OPTIONS = [
  { key: 'pass', label: 'pass' },
  { key: 'fail', label: 'fail' },
  { key: 'pending', label: 'pending' },
];
const SORT_OPTIONS = [
  { key: 'updated',    label: '최근 업데이트' },
  { key: 'created',    label: '생성일' },
  { key: 'asset_key',  label: 'asset_key' },
  { key: 'color_count',label: 'color_count' },
];
// URL query 유틸. history-router 기준으로 location.search 를 사용한다.
function _parseUrlQuery() {
  const search = typeof window !== 'undefined' ? (window.location.search || '') : '';
  const sp = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const out = {};
  for (const [k, v] of sp) out[k] = v;
  return out;
}

function _writeUrlQuery(params) {
  const path = typeof window !== 'undefined' ? (window.location.pathname || '') : '';
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== '') sp.set(k, String(v));
  }
  const qs = sp.toString();
  const next = qs ? `${path}?${qs}` : path;
  const current = typeof window !== 'undefined'
    ? `${window.location.pathname}${window.location.search || ''}`
    : '';
  if (next !== current) {
    // replaceState 로 히스토리 스팸 방지.
    window.history.replaceState(null, '', next);
  }
}

function _toggleSetValue(setter, key) {
  setter((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
}

function _relativeTime(ts) {
  if (!ts) return 'time n/a';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return 'time n/a';
  const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diffSec < 60) return `${Math.max(1, diffSec)}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

function Assets() {
  const toasts = window.useToasts();
  const initial = useMemo(() => _parseUrlQuery(), []);

  const [projectSet, setProjectSet] = useState(() => new Set((initial.p || '').split(',').filter(Boolean)));
  const [statusSet, setStatusSet] = useState(() => new Set((initial.s || '').split(',').filter(Boolean)));
  const [validationSet, setValidationSet] = useState(() => new Set((initial.v || '').split(',').filter(Boolean)));
  const [categorySet, setCategorySet] = useState(() => new Set((initial.c || '').split(',').filter(Boolean)));
  const [q, setQ] = useState(initial.q || '');
  const [sortKey, setSortKey] = useState(initial.sort || 'updated');
  const [sortDir, setSortDir] = useState(initial.dir === 'asc' ? 'asc' : 'desc');
  const [visibleLimit, setVisibleLimit] = useState(Number(initial.limit) > 0 ? Number(initial.limit) : 96);

  const [selected, setSelected] = useState(() => new Set());
  const lastAnchorIdx = useRef(null);
  const gridRef = useRef(null);
  const sentinelRef = useRef(null);

  // URL 동기화 — 필터/검색/정렬/페이지 바뀔 때마다 search 업데이트.
  useEffect(() => {
    _writeUrlQuery({
      p: Array.from(projectSet).join(','),
      s: Array.from(statusSet).join(','),
      v: Array.from(validationSet).join(','),
      c: Array.from(categorySet).join(','),
      q,
      sort: sortKey !== 'updated' ? sortKey : '',
      dir: sortDir !== 'desc' ? sortDir : '',
      limit: visibleLimit !== 96 ? visibleLimit : '',
    });
  }, [projectSet, statusSet, validationSet, categorySet, q, sortKey, sortDir, visibleLimit]);

  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);

  const summary = window.useAsync(() => window.api.assetSummary(), []);

  const assets = window.useAsync(() => window.api.listAssets({}), []);

  const onSseBatch = useCallback((batch) => {
    if (!Array.isArray(batch) || !batch.length) return;
    const kinds = new Set([
      'asset_approved_from_candidate',
      'asset_approve_undone',
      'asset_candidate_selected',
      'asset_status_changed',
      'asset_regenerate_queued',
      'asset_history_restored',
      'validation_updated',
    ]);
    for (const e of batch) {
      if (!e || typeof e !== 'object') continue;
      if (!kinds.has(e.type)) continue;
      summary.reload();
      assets.reload();
      return;
    }
  }, [summary, assets]);
  window.useSSE?.(onSseBatch);

  // 필터 변경 시 선택/표시 상한 초기화.
  useEffect(() => {
    setSelected(new Set());
    setVisibleLimit(96);
    lastAnchorIdx.current = null;
  }, [projectSet, statusSet, validationSet, categorySet, q, sortKey, sortDir]);

  const filtered = useMemo(() => {
    const xs = assets.data || [];
    const needle = q.trim().toLowerCase();
    const base = xs.filter((a) => {
      const projectOk = projectSet.size === 0 || projectSet.has(a.project || '');
      const statusOk = statusSet.size === 0 || statusSet.has(a.status || '');
      const validationOk = validationSet.size === 0 || validationSet.has(a.validation_status || '');
      const categoryOk = categorySet.size === 0 || categorySet.has(a.category || '');
      if (!(projectOk && statusOk && validationOk && categoryOk)) return false;
      if (!needle) return true;
      return (a.asset_key || '').toLowerCase().includes(needle)
        || (a.category || '').toLowerCase().includes(needle)
        || (a.id || '').toLowerCase().includes(needle);
    });
    const dir = sortDir === 'asc' ? 1 : -1;
    const getKey = (a) => {
      switch (sortKey) {
        case 'created':     return a.created_at || '';
        case 'asset_key':   return a.asset_key || '';
        case 'color_count': return Number(a.color_count || 0);
        case 'updated':
        default:            return a.updated_at || a.created_at || '';
      }
    };
    return [...base].sort((a, b) => {
      const ka = getKey(a), kb = getKey(b);
      if (ka < kb) return -1 * dir;
      if (ka > kb) return 1 * dir;
      return 0;
    });
  }, [assets.data, projectSet, statusSet, validationSet, categorySet, q, sortKey, sortDir]);

  const rows = useMemo(() => filtered.slice(0, visibleLimit), [filtered, visibleLimit]);

  useEffect(() => {
    if (!sentinelRef.current) return undefined;
    if (visibleLimit >= filtered.length) return undefined;
    const obs = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setVisibleLimit((n) => Math.min(filtered.length, n + 96));
      }
    }, { rootMargin: '200px' });
    obs.observe(sentinelRef.current);
    return () => obs.disconnect();
  }, [visibleLimit, filtered.length]);

  // shift-click 다중 선택을 위한 anchor 관리.
  const toggle = useCallback((id, idx, opts = {}) => {
    setSelected((s) => {
      const next = new Set(s);
      if (opts.shift && lastAnchorIdx.current != null) {
        const [from, to] = lastAnchorIdx.current < idx
          ? [lastAnchorIdx.current, idx] : [idx, lastAnchorIdx.current];
        for (let i = from; i <= to; i++) next.add(rows[i]?.id);
        next.delete(undefined);
      } else {
        if (next.has(id)) next.delete(id); else next.add(id);
        lastAnchorIdx.current = idx;
      }
      return next;
    });
  }, [rows]);

  // shift + ↑↓ 키보드 다중 선택. 현재 앵커 기준으로 ±1.
  window.useKeyboard({
    ArrowDown: (e) => {
      if (!e.shiftKey) return;
      e.preventDefault?.();
      setSelected((s) => {
        const anchor = lastAnchorIdx.current ?? -1;
        const nextIdx = Math.min(rows.length - 1, anchor + 1);
        const next = new Set(s);
        const id = rows[nextIdx]?.id;
        if (id) next.add(id);
        lastAnchorIdx.current = nextIdx;
        return next;
      });
    },
    ArrowUp: (e) => {
      if (!e.shiftKey) return;
      e.preventDefault?.();
      setSelected((s) => {
        const anchor = lastAnchorIdx.current ?? rows.length;
        const nextIdx = Math.max(0, anchor - 1);
        const next = new Set(s);
        const id = rows[nextIdx]?.id;
        if (id) next.add(id);
        lastAnchorIdx.current = nextIdx;
        return next;
      });
    },
    Escape: () => { setSelected(new Set()); },
  }, [rows]);

  async function runBulkApi(fn, label) {
    try {
      const res = await fn();
      toasts.push({
        kind: 'success',
        message: `${label} 완료${res?.count != null ? ` · ${res.count}건` : ''}`,
      });
      summary.reload(); assets.reload();
    } catch (e) {
      toasts.push({ kind: 'error', message: `${label} 실패: ` + (e.message || e) });
    }
  }

  async function bulkPatchStatus(nextStatus, label) {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    let ok = 0, fail = 0;
    for (const id of ids) {
      try {
        await window.api.patchAssetStatus(id, nextStatus);
        ok++;
      } catch (_) { fail++; }
    }
    toasts.push({
      kind: fail === 0 ? 'success' : (ok === 0 ? 'error' : 'info'),
      message: `${label}: ${ok}건 성공${fail ? `, ${fail}건 실패` : ''}`,
    });
    setSelected(new Set());
    summary.reload(); assets.reload();
  }

  async function bulkValidateSelected() {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    let ok = 0, fail = 0;
    for (const id of ids) {
      try {
        await window.api.validateAsset(id);
        ok++;
      } catch (_) { fail++; }
    }
    toasts.push({
      kind: fail === 0 ? 'success' : 'info',
      message: `재검증: ${ok}건 성공${fail ? `, ${fail}건 실패` : ''}`,
    });
    summary.reload(); assets.reload();
  }

  async function bulkRegenerateSelected() {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    let ok = 0, fail = 0;
    for (const id of ids) {
      try {
        await window.api.regenerateAsset(id);
        ok++;
      } catch (_) { fail++; }
    }
    toasts.push({
      kind: fail === 0 ? 'success' : 'info',
      message: `재생성 큐 등록: ${ok}건${fail ? ` (${fail}건 실패)` : ''}`,
    });
    summary.reload(); assets.reload();
  }

  const byStatus = summary.data?.by_status || {};
  const byValidation = summary.data?.by_validation || {};
  const byCategory = summary.data?.by_category || {};

  return (
    <div>
      <window.PageToolbar
        left={
          <>
            <span className="chip">
              <b>{filtered.length}</b>
              <span style={{ opacity: 0.6 }}>/ 총 {summary.data?.total ?? '—'}</span>
            </span>
            <span className="chip" style={{ opacity: 0.7 }}>
              shown {rows.length}/{filtered.length}
            </span>
          </>
        }
        right={
          <>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>sort</label>
            <select
              className="input"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value)}
              style={{ width: 160 }}
            >
              {SORT_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
            <button
              className="btn"
              onClick={() => setSortDir((d) => d === 'asc' ? 'desc' : 'asc')}
              title={sortDir === 'asc' ? '오름차순' : '내림차순'}
            >{sortDir === 'asc' ? '↑' : '↓'}</button>
            <input
              className="input"
              placeholder="asset_key / category / id 검색…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 220 }}
            />
            <button className="btn" onClick={() => { summary.reload(); assets.reload(); }} title="새로고침">↻</button>
          </>
        }
        info={{
          title: 'assets',
          text: '각 asset_key 의 canonical pick (승인된 1장). 썸네일 클릭 = 상세, shift/⌘+click 또는 shift+↑↓ = 다중 선택. 필터/정렬 상태는 URL 에 자동 직렬화.',
        }}
      />

      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <aside className="filter-side panel-card" style={{ position: 'sticky', top: 16 }}>
          <h4>Project</h4>
          <label className="row">
            <input type="checkbox" checked={projectSet.size === 0} onChange={() => setProjectSet(new Set())}/>
            <span>전체</span>
          </label>
          {(projects.data?.items || []).map((p) => {
            // 새 schema: {slug, display_name, archived_at, ...}. archived 는 dim
            // 처리 — read 는 가능하지만 새 write 거부 시각 단서 (DESIGN.md A11y).
            const slug = p.slug;
            const label = p.display_name || p.slug;
            const archived = !!p.archived_at;
            return (
              <label key={slug} className="row" style={{ opacity: archived ? 0.55 : 1 }}>
                <input type="checkbox" checked={projectSet.has(slug)} onChange={() => _toggleSetValue(setProjectSet, slug)}/>
                <span>{label}{archived ? ' ⊘' : ''}</span>
              </label>
            );
          })}

          <h4>Status</h4>
          <label className="row">
            <input type="checkbox" checked={statusSet.size === 0} onChange={() => setStatusSet(new Set())}/>
            <span>전체 <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{summary.data?.total || 0}</b></span>
          </label>
          {STATUS_OPTIONS.map((opt) => (
            <label key={opt.key} className="row">
              <input type="checkbox" checked={statusSet.has(opt.key)} onChange={() => _toggleSetValue(setStatusSet, opt.key)}/>
              <span>{opt.label} <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{byStatus[opt.key] || 0}</b></span>
            </label>
          ))}

          <h4>Validation</h4>
          <label className="row">
            <input type="checkbox" checked={validationSet.size === 0} onChange={() => setValidationSet(new Set())}/>
            <span>전체</span>
          </label>
          {VALIDATION_OPTIONS.map((opt) => (
            <label key={opt.key} className="row">
              <input type="checkbox" checked={validationSet.has(opt.key)} onChange={() => _toggleSetValue(setValidationSet, opt.key)}/>
              <span>{opt.label} <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{byValidation[opt.key] || 0}</b></span>
            </label>
          ))}

          <h4>Category</h4>
          <label className="row">
            <input type="checkbox" checked={categorySet.size === 0} onChange={() => setCategorySet(new Set())}/>
            <span>전체</span>
          </label>
          {Object.entries(byCategory).map(([k, count]) => (
            <label key={k} className="row">
              <input type="checkbox" checked={categorySet.has(k)} onChange={() => _toggleSetValue(setCategorySet, k)}/>
              <span>{k} <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{count}</b></span>
            </label>
          ))}
        </aside>

        <main style={{ flex: 1, minWidth: 0 }}>
          <window.ErrorPanel error={assets.error} onRetry={assets.reload}/>
          {assets.loading && !assets.data && <window.Skeleton height={240}/>}
          {assets.data && rows.length === 0 && (
            <window.EmptyState title="조건과 맞는 에셋 없음" hint="좌측 필터를 완화하거나 검색어를 지워보세요."/>
          )}
          {rows.length > 0 && (
            <div className="asset-grid" ref={gridRef} role="grid" aria-label="assets">
              {rows.map((a, idx) => (
                <AssetCard
                  key={a.id}
                  a={a}
                  selected={selected.has(a.id)}
                  onSelect={(opts) => toggle(a.id, idx, opts)}
                />
              ))}
            </div>
          )}

          {filtered.length > rows.length && (
            <div style={{ marginTop: 12, display: 'flex', justifyContent: 'center' }}>
              <button
                className="btn"
                onClick={() => setVisibleLimit((n) => Math.min(filtered.length, n + 96))}
              >
                더 불러오기 ({rows.length}/{filtered.length})
              </button>
            </div>
          )}
          <div ref={sentinelRef} style={{ height: 1 }} />

          {(selected.size > 0 || (validationSet.has('fail') && rows.length > 0)) && (
            <div className="bulk-bar">
              {selected.size > 0 ? (
                <>
                  <span>선택 <b>{selected.size}</b>건</span>
                  <button className="btn" onClick={() => setSelected(new Set())}>선택 해제</button>
                  <div style={{ flex: 1 }}/>
                  <button
                    className="btn"
                    onClick={() => bulkPatchStatus('approved', '승인')}
                    title="선택한 에셋 status='approved'"
                  >✓ 승인</button>
                  <button
                    className="btn"
                    onClick={() => bulkPatchStatus('rejected', '거부')}
                    title="선택한 에셋 status='rejected'"
                  >✕ 거부</button>
                  <button
                    className="btn"
                    onClick={bulkValidateSelected}
                    title="선택한 에셋 재검증"
                  >재검증</button>
                  <button
                    className="btn btn-primary"
                    onClick={bulkRegenerateSelected}
                    title="선택한 에셋 재생성 큐"
                  >재생성</button>
                </>
              ) : (
                <>
                  <span>검증 실패 <b>{byValidation.fail || 0}</b>건</span>
                  <div style={{ flex: 1 }}/>
                  <button
                    className="btn"
                    onClick={() => runBulkApi(() => window.api.revalidateFailed(undefined), '재검증')}
                  >재검증</button>
                  <button
                    className="btn btn-primary"
                    onClick={() => runBulkApi(() => window.api.regenerateFailed(undefined), '재생성')}
                  >실패분 재생성</button>
                </>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function AssetCard({ a, selected, onSelect }) {
  const dot =
    a.status === 'approved' ? 'approved'
    : a.status === 'rejected' ? 'rejected'
    : 'pending';
  const v = a.validation_status;
  const imgUrl = a.image_url || (a.id ? `/api/assets/${a.id}/image` : null);

  return (
    <div className={`asset-card ${selected ? 'selected' : ''}`} role="gridcell" aria-selected={selected}>
      <div
        className="thumb-box"
        onClick={(e) => {
          if (e.shiftKey || e.metaKey || e.ctrlKey) {
            onSelect({ shift: e.shiftKey });
            return;
          }
          window.navigate(`/assets/${a.id}`);
        }}
        title="click = 상세, shift/⌘+click = 선택, shift+↑↓ = 다중 선택"
      >
        {imgUrl ? <img src={imgUrl} alt={a.asset_key} loading="lazy"/> : <div className="placeholder">…</div>}
        <span className={`dot ${dot}`}/>
        {v === 'pass' && (
          <span className="vbdg pass" title={a.validation_message || 'validation=pass'}>PASS</span>
        )}
        {v === 'fail' && (
          <span
            className="vbdg fail"
            title={a.validation_message || 'validation=fail'}
            style={{ cursor: 'help' }}
          >FAIL</span>
        )}
      </div>
      <div className="strip">
        <span className="asset-key" title={a.asset_key}>{a.asset_key}</span>
        <span className="meta">
          seed {a.generation_seed ?? '—'} · {_relativeTime(a.updated_at || a.created_at)}
        </span>
      </div>
      <div className="strip" style={{ marginTop: 2 }}>
        <span className="meta">
          {a.width}×{a.height}
          {typeof a.color_count === 'number' ? ` · colors ${a.color_count}` : ''}
        </span>
      </div>
    </div>
  );
}

window.Assets = Assets;

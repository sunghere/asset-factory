/* Assets — gallery of the canonical picks (one row per asset_key).
     - Left: filter sidebar (project / status / validation / category),
       driven by /api/assets/summary so counts are always truthful.
     - Right: responsive image grid with status dot + validation badge.
     - Bulk actions: click cards to select; action bar appears at the
       bottom and operates on the selection or on the current filter.

   The "bulk action" buttons route to existing backend endpoints:
     - 재검증 실패분     → POST /api/batch/revalidate-failed?project=…
     - 실패분 재생성    → POST /api/batch/regenerate-failed?project=…
     - 전체 재검증      → POST /api/validate/all?project=…
   Per-asset actions (validate / regenerate / approve toggle) live in
   AssetDetail.jsx. */

const { useState, useMemo, useEffect } = React;

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

function Assets() {
  const toasts = window.useToasts();

  const [project, setProject] = useState('');
  const [status, setStatus] = useState('');
  const [validation, setValidation] = useState('');
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState(() => new Set());

  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);

  const summary = window.useAsync(
    () => window.api.assetSummary(project || undefined),
    [project],
  );

  const assets = window.useAsync(
    () => window.api.listAssets({
      project: project || undefined,
      status: status || undefined,
      validation_status: validation || undefined,
      category: category || undefined,
    }),
    [project, status, validation, category],
  );

  // Changing filters usually invalidates the selection.
  useEffect(() => { setSelected(new Set()); }, [project, status, validation, category]);

  const rows = useMemo(() => {
    const xs = assets.data || [];
    const needle = q.trim().toLowerCase();
    if (!needle) return xs;
    return xs.filter((a) =>
      (a.asset_key || '').toLowerCase().includes(needle)
      || (a.category || '').toLowerCase().includes(needle)
      || (a.id || '').toLowerCase().includes(needle)
    );
  }, [assets.data, q]);

  function toggle(id) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function runBulk(fn, label) {
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

  const byStatus = summary.data?.by_status || {};
  const byValidation = summary.data?.by_validation || {};
  const byCategory = summary.data?.by_category || {};

  return (
    <div>
      <window.PageToolbar
        left={
          <span className="chip">
            <b>{rows.length}</b>
            <span style={{ opacity: 0.6 }}>/ 총 {summary.data?.total ?? '—'}</span>
          </span>
        }
        right={
          <>
            <input
              className="input"
              placeholder="asset_key / category / id 검색…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 240 }}
            />
            <button className="btn" onClick={() => { summary.reload(); assets.reload(); }} title="새로고침">↻</button>
          </>
        }
        info={{
          title: 'assets',
          text: '각 asset_key 의 canonical pick (승인된 1장). 썸네일 클릭 = 상세, shift/⌘+click = 선택. 좌측 필터는 status / validation / category 로 교차 필터링.',
        }}
      />

      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <aside className="filter-side panel-card" style={{ position: 'sticky', top: 16 }}>
          <h4>Project</h4>
          <label className="row">
            <input type="radio" checked={!project} onChange={() => setProject('')}/>
            <span>전체</span>
          </label>
          {(projects.data?.items || []).map((p) => {
            const id = typeof p === 'string' ? p : (p.id ?? p.name ?? String(p));
            const label = typeof p === 'string' ? p : (p.name ?? p.id ?? String(p));
            return (
              <label key={id} className="row">
                <input type="radio" checked={project === id} onChange={() => setProject(id)}/>
                <span>{label}</span>
              </label>
            );
          })}

          <h4>Status</h4>
          <label className="row">
            <input type="radio" checked={!status} onChange={() => setStatus('')}/>
            <span>전체 <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{summary.data?.total || 0}</b></span>
          </label>
          {STATUS_OPTIONS.map((opt) => (
            <label key={opt.key} className="row">
              <input type="radio" checked={status === opt.key} onChange={() => setStatus(opt.key)}/>
              <span>{opt.label} <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{byStatus[opt.key] || 0}</b></span>
            </label>
          ))}

          <h4>Validation</h4>
          <label className="row">
            <input type="radio" checked={!validation} onChange={() => setValidation('')}/>
            <span>전체</span>
          </label>
          {VALIDATION_OPTIONS.map((opt) => (
            <label key={opt.key} className="row">
              <input type="radio" checked={validation === opt.key} onChange={() => setValidation(opt.key)}/>
              <span>{opt.label} <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{byValidation[opt.key] || 0}</b></span>
            </label>
          ))}

          <h4>Category</h4>
          <label className="row">
            <input type="radio" checked={!category} onChange={() => setCategory('')}/>
            <span>전체</span>
          </label>
          {Object.entries(byCategory).map(([k, count]) => (
            <label key={k} className="row">
              <input type="radio" checked={category === k} onChange={() => setCategory(k)}/>
              <span>{k} <b style={{ color: 'var(--text-faint)', marginLeft: 4 }}>{count}</b></span>
            </label>
          ))}
        </aside>

        <main style={{ flex: 1, minWidth: 0 }}>
          {assets.error && (
            <div className="error-banner" style={{ marginBottom: 12 }}>
              <span>⚠</span><span>{String(assets.error.message || assets.error)}</span>
            </div>
          )}
          {assets.loading && !assets.data && <window.Skeleton height={240}/>}
          {assets.data && rows.length === 0 && (
            <window.EmptyState title="조건과 맞는 에셋 없음" hint="좌측 필터를 완화하거나 검색어를 지워보세요."/>
          )}
          {rows.length > 0 && (
            <div className="asset-grid">
              {rows.map((a) => (
                <AssetCard key={a.id} a={a} selected={selected.has(a.id)} onToggle={() => toggle(a.id)}/>
              ))}
            </div>
          )}

          {(selected.size > 0 || (validation === 'fail' && rows.length > 0)) && (
            <div className="bulk-bar">
              {selected.size > 0 ? (
                <>
                  <span>선택 <b>{selected.size}</b>건</span>
                  <button className="btn" onClick={() => setSelected(new Set())}>선택 해제</button>
                  <div style={{ flex: 1 }}/>
                  <span style={{ color: 'var(--text-faint)' }}>
                    개별 액션은 에셋 상세에서. 일괄 API는 아래 버튼 사용.
                  </span>
                </>
              ) : (
                <>
                  <span>검증 실패 <b>{byValidation.fail || 0}</b>건</span>
                  <div style={{ flex: 1 }}/>
                  <button
                    className="btn"
                    onClick={() => runBulk(() => window.api.revalidateFailed(project || undefined), '재검증')}
                  >재검증</button>
                  <button
                    className="btn btn-primary"
                    onClick={() => runBulk(() => window.api.regenerateFailed(project || undefined), '재생성')}
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

function AssetCard({ a, selected, onToggle }) {
  const dot =
    a.status === 'approved' ? 'approved'
    : a.status === 'rejected' ? 'rejected'
    : 'pending';
  const v = a.validation_status;
  const imgUrl = a.image_url || (a.id ? `/api/assets/${a.id}/image` : null);

  return (
    <div className={`asset-card ${selected ? 'selected' : ''}`}>
      <div
        className="thumb-box"
        onClick={(e) => {
          // Shift-click selects, plain click opens detail.
          if (e.shiftKey || e.metaKey || e.ctrlKey) { onToggle(); return; }
          window.navigate(`/assets/${a.id}`);
        }}
        title="click = 상세, shift/⌘+click = 선택"
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
        <span className="meta">{a.width}×{a.height}</span>
      </div>
    </div>
  );
}

window.Assets = Assets;

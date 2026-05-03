/* Export — copy approved assets to a target directory (+ optional manifest).
   Matches ExportRequest(project?, category?, since?, output_dir, save_manifest).
   미리보기는 /api/export/manifest 를 그대로 사용하고, 응답에 포함된 size_bytes
   합계로 실시간 용량(MB) 을 표시한다. 파일 트리는 project/category 로 그루핑
   하여 실제 복사될 디렉토리 구조를 한눈에 보여준다. */

const { useState, useMemo, useCallback } = React;

function _fmtBytes(n) {
  if (n == null || Number.isNaN(n)) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function _sinceChoices() {
  // 상단 4개 preset + custom. value 는 ISO8601 문자열(UTC) 로 직렬화.
  const now = new Date();
  const iso = (d) => new Date(d).toISOString();
  return [
    { label: '전체', value: '' },
    { label: '최근 24시간', value: iso(now.getTime() - 24 * 3600 * 1000) },
    { label: '최근 7일', value: iso(now.getTime() - 7 * 24 * 3600 * 1000) },
    { label: '최근 30일', value: iso(now.getTime() - 30 * 24 * 3600 * 1000) },
  ];
}

function _groupTree(items) {
  // { project: { category: [items...] } }
  const tree = new Map();
  for (const it of items) {
    if (!tree.has(it.project)) tree.set(it.project, new Map());
    const byCat = tree.get(it.project);
    if (!byCat.has(it.category)) byCat.set(it.category, []);
    byCat.get(it.category).push(it);
  }
  return tree;
}

function FileTree({ tree, outputRoot }) {
  // 순수 표시용 — project/category 별 접힘 가능한 <details> 로 내보낸다.
  return (
    <div style={{ padding: 12, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
      <div style={{ color: 'var(--text-faint)', marginBottom: 8 }}>{outputRoot || '(output_dir)'}/</div>
      {[...tree.entries()].map(([project, byCat]) => {
        const projTotal = [...byCat.values()].reduce((a, arr) => a + arr.length, 0);
        return (
          <details key={project} open style={{ marginBottom: 6 }}>
            <summary style={{ cursor: 'pointer', color: 'var(--text)' }}>
              📁 {project}/ <span className="hint" style={{ marginLeft: 6 }}>{projTotal}</span>
            </summary>
            <div style={{ paddingLeft: 16 }}>
              {[...byCat.entries()].map(([category, arr]) => (
                <details key={category} style={{ marginTop: 4 }}>
                  <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>
                    📂 {category}/ <span className="hint" style={{ marginLeft: 6 }}>{arr.length}</span>
                  </summary>
                  <div style={{ paddingLeft: 16, color: 'var(--text-faint)' }}>
                    {arr.slice(0, 50).map((it) => (
                      <div key={it.asset_key} title={it.path}>
                        🖼 {it.asset_key}.png
                        <span className="hint" style={{ marginLeft: 8 }}>
                          {it.width}×{it.height}
                          {it.size_bytes != null ? ` · ${_fmtBytes(it.size_bytes)}` : ''}
                        </span>
                      </div>
                    ))}
                    {arr.length > 50 && (
                      <div className="hint" style={{ marginTop: 4 }}>
                        … {arr.length - 50}개 더
                      </div>
                    )}
                  </div>
                </details>
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function Export() {
  const toasts = window.useToasts();
  const [project, setProject] = useState('');
  const [category, setCategory] = useState('');
  const [sincePreset, setSincePreset] = useState('');
  const [outputDir, setOutputDir] = useState('~/workspace/assets');
  const [saveManifest, setSaveManifest] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [view, setView] = useState('tree'); // 'tree' | 'table'
  const [lastExport, setLastExport] = useState(null);

  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);
  const summary = window.useAsync(
    () => window.api.assetSummary(project || undefined).catch(() => null),
    [project],
  );

  const since = sincePreset;
  const manifest = window.useAsync(
    () =>
      window.api.getManifest({
        project: project || undefined,
        category: category || undefined,
        since: since || undefined,
      }),
    [project, category, since],
  );

  const onSseBatch = useCallback((batch) => {
    if (!Array.isArray(batch) || !batch.length) return;
    for (const e of batch) {
      if (e && e.type === 'export_completed') {
        summary.reload();
        manifest.reload();
        return;
      }
    }
  }, [summary, manifest]);
  window.useSSE?.(onSseBatch);

  async function doExport() {
    setExporting(true);
    try {
      const res = await window.api.runExport({
        project: project || null,
        category: category || null,
        since: since || null,
        output_dir: outputDir,
        save_manifest: saveManifest,
      });
      setLastExport(res);
      toasts.push({
        kind: 'success',
        message: `내보내기 완료 · ${res.exported_count}개 → ${res.output_dir}`,
        ttl: 8000,
      });
    } catch (e) {
      toasts.push({ kind: 'error', message: '내보내기 실패: ' + (e.message || e), ttl: 8000 });
    } finally {
      setExporting(false);
    }
  }

  const items = manifest.data?.items || [];
  const totalBytes = manifest.data?.total_bytes ?? items.reduce((a, it) => a + (it.size_bytes || 0), 0);
  const tree = useMemo(() => _groupTree(items), [items]);
  const categoryOptions = useMemo(() => {
    const byCat = summary.data?.by_category || {};
    return Object.keys(byCat).sort();
  }, [summary.data]);

  return (
    <div>
      <window.PageToolbar
        left={
          <>
            <span className="chip">
              미리보기 <b>{items.length}</b>건
            </span>
            <span className="chip" title="선택 승인본 총 용량">
              총 <b>{_fmtBytes(totalBytes)}</b>
            </span>
          </>
        }
        right={<button className="btn" onClick={manifest.reload} title="새로고침">↻</button>}
        info={{
          title: 'export',
          text: '승인된 assets 를 지정 디렉토리로 복사. project/category/since 로 필터 가능하며, save_manifest 체크 시 asset-manifest.json 동봉. 경로는 서버 allowlist 내부만 허용.',
        }}
      />

      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '300px 1fr 320px', alignItems: 'start' }}>
        <div className="panel-card">
          <h3>필터</h3>
          <div className="form-grid">
            <label style={{ gridColumn: 'span 2' }}>
              <span>프로젝트 <span className="hint">(빈 값 = 전체)</span></span>
              <select className="input" value={project} onChange={(e) => setProject(e.target.value)}>
                <option value="">— 전체 프로젝트 —</option>
                {(projects.data?.items || []).map((p) => (
                  // 새 schema {slug, display_name}. archived 도 export 대상
                  // (read-only 데이터도 내려받을 수 있어야 함) — 라벨에 ⊘ 표시.
                  <option key={p.slug} value={p.slug}>
                    {p.display_name || p.slug}{p.archived_at ? ' ⊘' : ''}
                  </option>
                ))}
              </select>
            </label>

            <label style={{ gridColumn: 'span 2' }}>
              <span>카테고리 <span className="hint">(빈 값 = 전체)</span></span>
              <select
                className="input"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={categoryOptions.length === 0}
              >
                <option value="">— 전체 카테고리 —</option>
                {categoryOptions.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>

            <label style={{ gridColumn: 'span 2' }}>
              <span>since</span>
              <select
                className="input"
                value={sincePreset}
                onChange={(e) => setSincePreset(e.target.value)}
              >
                {_sinceChoices().map((c) => (
                  <option key={c.label} value={c.value}>{c.label}</option>
                ))}
              </select>
              {sincePreset && (
                <span className="hint" style={{ marginTop: 4, display: 'block' }}>
                  {new Date(sincePreset).toLocaleString()} 이후 업데이트된 승인본만
                </span>
              )}
            </label>

            <label style={{ gridColumn: 'span 2' }}>
              <span>출력 디렉토리</span>
              <input
                className="input"
                value={outputDir}
                onChange={(e) => setOutputDir(e.target.value)}
                placeholder="~/workspace/assets"
              />
            </label>
          </div>
        </div>

        <div className="panel-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div
            style={{
              padding: '12px 16px',
              borderBottom: '1px solid var(--border-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            <h3 style={{ margin: 0 }}>
              미리보기 <span className="hint">{items.length}건 · {_fmtBytes(totalBytes)}</span>
            </h3>
            <div style={{ display: 'flex', gap: 4 }}>
              <button
                className={`btn btn-sm${view === 'tree' ? ' btn-primary' : ''}`}
                onClick={() => setView('tree')}
                title="파일 트리"
              >
                트리
              </button>
              <button
                className={`btn btn-sm${view === 'table' ? ' btn-primary' : ''}`}
                onClick={() => setView('table')}
                title="표 형태"
              >
                표
              </button>
              {manifest.loading && <span className="hint" style={{ alignSelf: 'center' }}>로드 중…</span>}
            </div>
          </div>

          {lastExport && (
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-elev-2)' }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                결과 · {lastExport.exported_count}개 → {lastExport.output_dir}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)', marginTop: 4 }}>
                manifest: {lastExport.manifest_path || lastExport.manifest_url || (saveManifest ? 'saved (path not returned)' : 'disabled')}
              </div>
            </div>
          )}

          {manifest.error ? (
            <div style={{ padding: 16 }}>
              <div className="error-banner">
                <span>⚠</span><span>{String(manifest.error.message || manifest.error)}</span>
              </div>
            </div>
          ) : items.length === 0 ? (
            <div style={{ padding: 40 }}>
              <window.EmptyState title="내보낼 승인본이 없습니다" hint="필터를 조정하거나 /cherry-pick에서 승인하세요." />
            </div>
          ) : view === 'tree' ? (
            <div style={{ maxHeight: 520, overflow: 'auto' }}>
              <FileTree tree={tree} outputRoot={outputDir} />
            </div>
          ) : (
            <div style={{ maxHeight: 520, overflow: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>project</th>
                    <th>category</th>
                    <th>asset_key</th>
                    <th>dims</th>
                    <th>size</th>
                    <th>sha256</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr key={`${it.project}/${it.asset_key}/${i}`}>
                      <td>{it.project}</td>
                      <td style={{ color: 'var(--text-muted)' }}>{it.category}</td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{it.asset_key}</td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                        {it.width}×{it.height}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                        {_fmtBytes(it.size_bytes)}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
                        {it.sha256 ? it.sha256.slice(0, 12) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <aside className="panel-card" style={{ position: 'sticky', top: 16 }}>
          <h3>내보내기</h3>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
            <input
              type="checkbox"
              checked={saveManifest}
              onChange={(e) => setSaveManifest(e.target.checked)}
            />
            <span>manifest 저장</span>
          </label>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', marginBottom: 10 }}>
            target: {outputDir || '(unset)'}
            <br />
            payload: {items.length} items · {_fmtBytes(totalBytes)}
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={doExport}
            disabled={exporting || items.length === 0}
            title={items.length === 0 ? '내보낼 승인본이 없습니다' : `${_fmtBytes(totalBytes)} 복사`}
            style={{ width: '100%' }}
          >
            {exporting ? '복사 중…' : '▶ 내보내기'}
          </button>
          <button type="button" className="btn" onClick={manifest.reload} style={{ width: '100%', marginTop: 8 }}>
            미리보기 새로고침
          </button>
          <p style={{ color: 'var(--text-faint)', fontSize: 11, marginTop: 10 }}>
            경로는 서버 allowlist 내부만 허용됩니다. 필요 시 디렉토리를 자동 생성합니다.
          </p>
        </aside>
      </div>
    </div>
  );
}

window.Export = Export;

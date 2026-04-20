/* Export — copy approved assets to a target directory (+ optional manifest).
   Matches ExportRequest(project?, output_dir, save_manifest). We also show
   a dry-run preview by fetching /api/export/manifest first, so the user
   sees what they're about to copy before committing to disk writes. */

const { useState } = React;

function Export() {
  const toasts = window.useToasts();
  const [project, setProject] = useState('');
  const [outputDir, setOutputDir] = useState('~/workspace/assets');
  const [saveManifest, setSaveManifest] = useState(true);
  const [exporting, setExporting] = useState(false);

  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);
  const manifest = window.useAsync(
    () => window.api.getManifest(project || undefined),
    [project],
  );

  async function doExport() {
    setExporting(true);
    try {
      const res = await window.api.runExport({
        project: project || null,
        output_dir: outputDir,
        save_manifest: saveManifest,
      });
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
  const totalBytes = 0; // server doesn't give size; we show count only

  return (
    <div>
      <window.PageToolbar
        left={<span className="chip">미리보기 <b>{items.length}</b>건</span>}
        right={<button className="btn" onClick={manifest.reload} title="새로고침">↻</button>}
        info={{
          title: 'export',
          text: '승인된 assets 를 지정 디렉토리로 복사. save_manifest 체크 시 asset-manifest.json 동봉. 경로는 서버 allowlist 내부만 허용.',
        }}
      />

      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1.4fr' }}>
        <div className="panel-card">
          <h3>대상 설정</h3>
          <div className="form-grid">
            <label style={{ gridColumn: 'span 2' }}>
              <span>프로젝트 <span className="hint">(빈 값 = 전체)</span></span>
              <select className="input" value={project} onChange={(e) => setProject(e.target.value)}>
                <option value="">— 전체 프로젝트 —</option>
                {(projects.data?.items || []).map((p) => {
                  const id = typeof p === 'string' ? p : (p.id ?? p.name ?? String(p));
                  const label = typeof p === 'string' ? p : (p.name ?? p.id ?? String(p));
                  return <option key={id} value={id}>{label}</option>;
                })}
              </select>
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
            <label style={{ gridColumn: 'span 2', display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={saveManifest}
                onChange={(e) => setSaveManifest(e.target.checked)}
              />
              <span>asset-manifest.json 생성</span>
            </label>
          </div>

          <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={doExport}
              disabled={exporting || items.length === 0}
            >
              {exporting ? '복사 중…' : `내보내기 (${items.length}건)`}
            </button>
            <button type="button" className="btn" onClick={manifest.reload}>미리보기 새로고침</button>
          </div>
          <p style={{ color: 'var(--text-faint)', fontSize: 11, marginTop: 10 }}>
            경로는 서버가 허용한 allowlist 내부여야 합니다. 없으면 자동 생성됩니다.
          </p>
        </div>

        <div className="panel-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <h3 style={{ margin: 0 }}>미리보기 <span className="hint">{items.length}건</span></h3>
            {manifest.loading && <span className="hint">로드 중…</span>}
          </div>

          {manifest.error ? (
            <div style={{ padding: 16 }}>
              <div className="error-banner">
                <span>⚠</span><span>{String(manifest.error.message || manifest.error)}</span>
              </div>
            </div>
          ) : items.length === 0 ? (
            <div style={{ padding: 40 }}>
              <window.EmptyState title="내보낼 승인본이 없습니다" hint="먼저 /cherry-pick에서 승인하세요."/>
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
      </div>
    </div>
  );
}

window.Export = Export;

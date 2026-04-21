/* AssetDetail — deep inspection for one asset.
   Layout: left panel = primary image + metadata + actions.
           right panel = tabs (candidates, history).
   Actions:
     - toggle approved/pending/rejected (optimistic, with undo toast)
     - validate  → POST /api/validate/{id}
     - regenerate → POST /api/assets/{id}/regenerate (queues a new batch)
     - swap primary from any candidate slot → POST /api/assets/{id}/select-candidate
*/

const { useState, useMemo, useCallback } = React;

function _fmtTs(ts) {
  return (ts || '').slice(0, 19).replace('T', ' ');
}

function AssetDetail({ assetId }) {
  const toasts = window.useToasts();
  const [tab, setTab] = useState('candidates');
  const [running, setRunning] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState(null); // history row to confirm
  const [zoomed, setZoomed] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [showMetadataJson, setShowMetadataJson] = useState(false);

  const detail = window.useAsync(() => window.api.getAssetDetail(assetId), [assetId]);
  const history = window.useAsync(
    () => window.api.getAssetHistory(assetId).catch(() => []),
    [assetId],
  );
  const cands = window.useAsync(
    () => window.api.getAssetCandidates(assetId).catch(() => []),
    [assetId],
  );

  // useSSE passes an array per flush (see hooks.jsx); same contract as Dashboard.
  const onSseBatch = useCallback((batch) => {
    if (!Array.isArray(batch) || !batch.length) return;
    const kinds = new Set([
      'asset_status_changed',
      'asset_candidate_selected',
      'asset_history_restored',
      'asset_approved_from_candidate',
      'asset_approve_undone',
      'validation_updated',
    ]);
    for (const e of batch) {
      if (!e || typeof e !== 'object') continue;
      if (e.asset_id && e.asset_id !== assetId) continue;
      if (kinds.has(e.type)) {
        detail.reload();
        history.reload();
        cands.reload();
        return;
      }
    }
  }, [assetId, detail, history, cands]);
  window.useSSE?.(onSseBatch);

  async function setStatus(status) {
    const prev = detail.data?.status;
    try {
      await window.api.patchAssetStatus(assetId, status);
      detail.reload();
      toasts.push({
        kind: status === 'approved' ? 'success' : 'info',
        message: `상태 → ${status}`,
        onUndo: prev ? async () => {
          await window.api.patchAssetStatus(assetId, prev);
          detail.reload();
        } : undefined,
      });
    } catch (e) {
      toasts.push({ kind: 'error', message: '상태 변경 실패: ' + (e.message || e) });
    }
  }

  async function undoApprove() {
    try {
      await window.api.undoApprove(assetId);
      detail.reload();
      toasts.push({ kind: 'success', message: '승인 취소 완료' });
    } catch (e) {
      toasts.push({ kind: 'error', message: '승인 취소 실패: ' + (e.message || e) });
    }
  }

  async function doValidate() {
    setRunning(true);
    try {
      await window.api.validateAsset(assetId);
      toasts.push({ kind: 'success', message: '검증 완료' });
      detail.reload();
    } catch (e) {
      toasts.push({ kind: 'error', message: '검증 실패: ' + (e.message || e) });
    } finally { setRunning(false); }
  }

  async function doRegenerate() {
    setRunning(true);
    try {
      const res = await window.api.regenerateAsset(assetId);
      toasts.push({
        kind: 'success',
        message: `재생성 배치 등록${res?.batch_id ? ` · ${res.batch_id}` : ''}`,
        ttl: 8000,
      });
      cands.reload();
    } catch (e) {
      toasts.push({ kind: 'error', message: '재생성 실패: ' + (e.message || e) });
    } finally { setRunning(false); }
  }

  async function promoteCandidate(c) {
    try {
      await window.api.selectAssetCandidate(assetId, { job_id: c.job_id, slot_index: c.slot_index });
      toasts.push({ kind: 'success', message: `primary → slot ${c.slot_index}` });
      detail.reload(); history.reload(); cands.reload();
    } catch (e) {
      toasts.push({ kind: 'error', message: 'primary 교체 실패: ' + (e.message || e) });
    }
  }

  async function confirmRestore() {
    const h = restoreTarget;
    if (!h) return;
    setRestoreTarget(null);
    setRunning(true);
    try {
      const res = await window.api.restoreAssetHistory(assetId, h.version);
      toasts.push({
        kind: 'success',
        message: `v${h.version} 복원 완료${res?.new_history_version ? ` · 현재 메인이 v${res.new_history_version}로 보존됨` : ''}`,
        ttl: 8000,
      });
      detail.reload(); history.reload(); cands.reload();
    } catch (e) {
      const msg = e?.body?.detail || e?.message || String(e);
      toasts.push({ kind: 'error', message: '복원 실패: ' + msg });
    } finally {
      setRunning(false);
    }
  }

  const a = detail.data;

  return (
    <div>
      <div className="screen-header">
        <div>
          <div className="eyebrow">
            <a href="/app/assets" onClick={(e) => { e.preventDefault(); window.navigate('/assets'); }} style={{ color: 'var(--text-muted)' }}>
              ← assets
            </a>
            {a ? ` / ${a.project || 'default'} / ${a.category || '—'}` : ''}
          </div>
          <h1>
            {a?.asset_key || assetId}
            <span className="hint" style={{ marginLeft: 10, fontFamily: 'var(--font-mono)' }}>{assetId}</span>
          </h1>
        </div>
      </div>

      {detail.loading && !a && <window.Skeleton height={320}/>}
      <window.ErrorPanel error={detail.error} onRetry={detail.reload}/>

      {a && (
        <div style={{ display: 'grid', gap: 20, gridTemplateColumns: 'minmax(360px, 1fr) minmax(380px, 1fr)', alignItems: 'start' }}>
          <div>
            <div
              style={{
                background: 'var(--bg-elev-3)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 8,
                padding: 8,
                textAlign: 'center',
              }}
            >
              <img
                src={`/api/assets/${assetId}/image`}
                alt={a.asset_key}
                style={{
                  maxWidth: '100%',
                  imageRendering: 'pixelated',
                  maxHeight: zoomed ? 680 : 360,
                  cursor: zoomed ? 'zoom-out' : 'zoom-in',
                }}
                onClick={() => setZoomed((z) => !z)}
              />
            </div>
            <div style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => setZoomed((z) => !z)}>
                {zoomed ? '축소 보기' : '확대 보기'}
              </button>
            </div>

            <div style={{ marginTop: 14 }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>HISTORY</div>
              <HistoryCarousel items={history.data || []} loading={history.loading} onRestore={(h) => setRestoreTarget(h)} disabled={running}/>
            </div>

            <div style={{ marginTop: 12, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <span className={`pill ${a.status === 'approved' ? 'pill-ok' : a.status === 'rejected' ? 'pill-fail' : ''}`}>
                {a.status || 'pending'}
              </span>
              <span className={`pill ${a.validation_status === 'pass' ? 'pill-ok' : a.validation_status === 'fail' ? 'pill-fail' : ''}`}>
                validation {a.validation_status || '—'}
              </span>
            </div>

            <div style={{ marginTop: 12, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {a.status !== 'approved'
                ? <button className="btn btn-primary" onClick={() => setStatus('approved')}>✓ 승인</button>
                : <button className="btn" onClick={undoApprove}>↶ 승인 취소</button>}
              {a.status !== 'rejected'
                ? <button className="btn" onClick={() => setStatus('rejected')}>✕ 리젝트</button>
                : <button className="btn" onClick={() => setStatus('pending')}>리젝 취소</button>}
              <button className="btn" onClick={doValidate} disabled={running}>재검증</button>
              <button className="btn" onClick={doRegenerate} disabled={running}>재생성</button>
            </div>

            <dl className="meta-block" style={{ marginTop: 16 }}>
              <dt>id</dt><dd style={{ fontSize: 11 }}>{a.id}</dd>
              <dt>dims</dt><dd>{a.width}×{a.height}</dd>
              <dt>colors</dt><dd>{a.color_count ?? '—'}</dd>
              <dt>alpha</dt><dd>{a.has_alpha ? 'yes' : 'no'}</dd>
              <dt>validation</dt>
              <dd style={{ color: a.validation_status === 'fail' ? 'var(--accent-reject)' : undefined }}>
                {a.validation_message || '—'}
              </dd>
              <dt>seed</dt><dd>{a.generation_seed ?? '—'}</dd>
              <dt>model</dt><dd style={{ fontSize: 11 }}>{a.generation_model || '—'}</dd>
              <dt>created</dt><dd style={{ fontSize: 11 }}>{_fmtTs(a.created_at)}</dd>
              <dt>updated</dt><dd style={{ fontSize: 11 }}>{_fmtTs(a.updated_at)}</dd>
            </dl>
            <div className="panel-card" style={{ marginTop: 12, padding: 10 }}>
              <button className="btn" onClick={() => setShowPrompt((v) => !v)}>
                {showPrompt ? 'prompt 접기' : 'prompt 펼치기'}
              </button>
              {showPrompt && (
                <pre style={{ margin: '10px 0 0 0', fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {a.generation_prompt || '—'}
                </pre>
              )}
            </div>
            <div className="panel-card" style={{ marginTop: 12, padding: 10 }}>
              <button className="btn" onClick={() => setShowMetadataJson((v) => !v)}>
                {showMetadataJson ? 'metadata_json 접기' : 'metadata_json 펼치기'}
              </button>
              {showMetadataJson && (
                <pre style={{ margin: '10px 0 0 0', fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {JSON.stringify(a.metadata_json || a.metadata || {}, null, 2)}
                </pre>
              )}
            </div>
          </div>

          <div>
            <div className="tabs">
              <button className={`tab ${tab === 'candidates' ? 'active' : ''}`} onClick={() => setTab('candidates')}>
                /candidates <span style={{ color: 'var(--text-faint)' }}>({(cands.data || []).length})</span>
              </button>
              <button className={`tab ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>
                /history <span style={{ color: 'var(--text-faint)' }}>({(history.data || []).length})</span>
              </button>
            </div>

            {tab === 'candidates' && <CandidatesTab items={cands.data || []} loading={cands.loading} onPromote={promoteCandidate}/>}
            {tab === 'history' && (
              <HistoryTab
                items={history.data || []}
                loading={history.loading}
                onRestore={(h) => setRestoreTarget(h)}
                disabled={running}
              />
            )}
          </div>
        </div>
      )}

      {restoreTarget && window.Dialog && (
        <window.Dialog
          title={`v${restoreTarget.version} 복원`}
          onClose={() => setRestoreTarget(null)}
          footer={(
            <>
              <button className="btn" onClick={() => setRestoreTarget(null)}>취소</button>
              <button className="btn btn-primary" onClick={confirmRestore} disabled={running} autoFocus>
                이 버전으로 복원
              </button>
            </>
          )}
        >
          <p style={{ margin: '0 0 10px 0' }}>
            <b>v{restoreTarget.version}</b> 스냅샷을 현재 primary 로 되돌립니다.
          </p>
          <ul style={{ margin: '0 0 10px 18px', padding: 0, fontSize: 12, color: 'var(--text-muted)' }}>
            <li>현재 primary 는 새로운 history 행(최신 version)으로 자동 보존됩니다.</li>
            <li>검증(validation)은 복원된 파일 기준으로 다시 계산됩니다.</li>
            <li>복원 직후 되돌리려면 바로 다음 version 을 다시 복원하면 됩니다.</li>
          </ul>
          <div style={{
            background: 'var(--bg-elev-3)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 6,
            padding: 8,
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
          }}>
            snapshot_at: {(restoreTarget.snapshot_at || restoreTarget.created_at || '').slice(0, 19).replace('T', ' ')}
            <br/>seed: {restoreTarget.generation_seed ?? '—'} · model: {restoreTarget.generation_model || '—'}
            <br/>validation: {restoreTarget.validation_status || '—'}
          </div>
        </window.Dialog>
      )}
    </div>
  );
}

function CandidatesTab({ items, loading, onPromote }) {
  if (loading && items.length === 0) return <window.Skeleton height={180}/>;
  if (items.length === 0) {
    return <window.EmptyState title="후보 없음" hint="최근 배치에서 생성된 후보가 없습니다. 재생성 버튼을 누르면 새 배치가 생깁니다."/>;
  }
  return (
    <div className="asset-grid">
      {items.map((c) => {
        const url = c.image_url
          || `/api/asset-candidates/image?project=${encodeURIComponent(c.project)}&asset_key=${encodeURIComponent(c.asset_key)}&job_id=${encodeURIComponent(c.job_id)}&slot_index=${c.slot_index}`;
        return (
          <div key={`${c.job_id}-${c.slot_index}`} className={`asset-card ${c.is_picked ? 'selected' : ''}`}>
            <div className="thumb-box">
              <img src={url} alt="" loading="lazy"/>
              {c.is_picked && <span className="vbdg pass">PRIMARY</span>}
            </div>
            <div className="strip">
              <span className="asset-key">slot {c.slot_index}</span>
              <button
                className={c.is_picked ? 'btn' : 'btn btn-primary'}
                onClick={() => onPromote(c)}
                disabled={c.is_picked}
                style={{ padding: '2px 8px', fontSize: 10 }}
              >{c.is_picked ? 'current' : 'primary로 교체'}</button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function HistoryCarousel({ items, loading, onRestore, disabled }) {
  if (loading && items.length === 0) return <window.Skeleton height={120}/>;
  if (items.length === 0) {
    return <window.EmptyState title="이력 없음" hint="primary 교체 / 재생성 이력이 여기에 쌓입니다."/>;
  }
  return (
    <div style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 2 }}>
      {items.map((h, idx) => {
        const imgUrl = h.image_url || (h.asset_id ? `/api/assets/${h.asset_id}/image?v=${h.version}` : null);
        return (
          <div
            key={h.version}
            className="panel-card"
            style={{
              minWidth: 142,
              maxWidth: 142,
              padding: 8,
              borderColor: idx === 0 ? 'var(--accent-info)' : 'var(--border-subtle)',
            }}
          >
            <div
              style={{
                height: 92,
                borderRadius: 4,
                overflow: 'hidden',
                background: 'var(--bg-elev-3)',
                border: '1px solid var(--border-subtle)',
                display: 'grid',
                placeItems: 'center',
              }}
            >
              {imgUrl ? (
                <img src={imgUrl} alt="" loading="lazy" style={{ maxWidth: '100%', maxHeight: '100%', imageRendering: 'pixelated' }} />
              ) : (
                <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>no preview</span>
              )}
            </div>
            <div style={{ marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              v{h.version} · {_fmtTs(h.snapshot_at || h.created_at)}
            </div>
            <div style={{ marginTop: 4 }}>
              <span className={`pill ${h.validation_status === 'pass' ? 'pill-ok' : h.validation_status === 'fail' ? 'pill-fail' : ''}`}>
                {h.validation_status || '—'}
              </span>
            </div>
            <button
              className="btn"
              style={{ marginTop: 6, width: '100%' }}
              onClick={() => onRestore(h)}
              disabled={disabled}
            >
              이 버전 복원
            </button>
          </div>
        );
      })}
    </div>
  );
}

function HistoryTab({ items, loading, onRestore, disabled }) {
  if (loading && items.length === 0) return <window.Skeleton height={180}/>;
  if (items.length === 0) {
    return <window.EmptyState title="이력 없음" hint="primary 교체 / 재생성 이력이 여기에 쌓입니다."/>;
  }
  return (
    <div className="panel-card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ width: 60 }}>version</th>
            <th>snapshot_at</th>
            <th>validation</th>
            <th>seed</th>
            <th>model</th>
            <th style={{ width: 110, textAlign: 'right' }}></th>
          </tr>
        </thead>
        <tbody>
          {items.map((h) => (
            <tr key={h.version}>
              <td style={{ fontFamily: 'var(--font-mono)' }}>v{h.version}</td>
              <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                {(h.snapshot_at || h.created_at || '').slice(0, 19).replace('T', ' ')}
              </td>
              <td>
                <span className={`pill ${h.validation_status === 'pass' ? 'pill-ok' : h.validation_status === 'fail' ? 'pill-fail' : ''}`}>
                  {h.validation_status || '—'}
                </span>
              </td>
              <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{h.generation_seed ?? '—'}</td>
              <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                {h.generation_model || '—'}
              </td>
              <td style={{ textAlign: 'right' }}>
                {onRestore && (
                  <button
                    className="btn"
                    style={{ padding: '2px 8px', fontSize: 11 }}
                    onClick={() => onRestore(h)}
                    disabled={disabled}
                    title={`v${h.version} 을 primary 로 복원`}
                  >
                    restore
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

window.AssetDetail = AssetDetail;

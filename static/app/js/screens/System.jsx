/* System — operational dashboard for ops:
     - app & SD health
     - GC last run metrics + manual trigger
     - candidate directory footprint (derived from gc status)

   Rendering philosophy:
     - pills for health, big numbers for bytes/files
     - "silent success" — we only yell on error
*/

const { useState } = React;

function fmtBytes(n) {
  if (n == null || !Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

function System() {
  const toasts = window.useToasts();
  const [running, setRunning] = useState(false);
  const [orphanScan, setOrphanScan] = useState(null);  // { orphans_found, sample, scanned_total }
  const [orphanBusy, setOrphanBusy] = useState(false);

  const health = window.useAsync(() => window.api.health(), []);
  // PLAN_comfyui_catalog.md Task 7 — ComfyUI 가 primary backend.
  // healthSd 는 a1111 deprecated 표시용으로만 유지.
  const comfyuiHealth = window.useAsync(
    () => window.api.comfyuiHealth().catch((err) => ({ ok: false, error: String(err) })),
    [],
  );
  const healthSd = window.useAsync(
    () => window.api.healthSd().catch((err) => ({ ok: false, error: String(err) })),
    [],
  );
  const gc = window.useAsync(() => window.api.gcStatus(), []);
  const dbStat = window.useAsync(() => window.api.systemDb(), []);
  const worker = window.useAsync(() => window.api.systemWorker(), []);
  const logs = window.useAsync(() => window.api.systemLogs({ limit: 50 }), []);

  // Light-weight polling — 큰 이벤트가 아닌 스냅샷 류 (worker heartbeat/DB 카운트)
  // 이므로 SSE 보다 폴링이 맞다.
  window.useInterval(() => {
    health.reload(); comfyuiHealth.reload(); healthSd.reload(); gc.reload();
    dbStat.reload(); worker.reload(); logs.reload();
  }, 8000);

  async function runGc() {
    setRunning(true);
    try {
      await window.api.runGc();
      toasts.push({ kind: 'success', message: 'GC 실행 완료' });
      gc.reload();
    } catch (e) {
      toasts.push({ kind: 'error', message: 'GC 실패: ' + (e.message || e) });
    } finally {
      setRunning(false);
    }
  }

  async function scanOrphans() {
    setOrphanBusy(true);
    try {
      const r = await window.api.gcOrphanCandidates({ dryRun: true, limit: 8 });
      setOrphanScan(r);
      toasts.push({
        kind: r.orphans_found > 0 ? 'warning' : 'success',
        message: r.orphans_found > 0
          ? `Orphan ${r.orphans_found}건 검출 (전체 ${r.scanned_total}건 스캔). 아래에서 확인 후 정리하세요.`
          : `Orphan 없음 (전체 ${r.scanned_total}건 스캔, 모두 정상).`,
      });
    } catch (e) {
      toasts.push({ kind: 'error', message: '스캔 실패: ' + (e.message || e) });
    } finally {
      setOrphanBusy(false);
    }
  }

  async function deleteOrphans() {
    if (!orphanScan || orphanScan.orphans_found <= 0) return;
    if (!window.confirm(
      `image_path 가 disk 에 없는 candidate ${orphanScan.orphans_found}건을 DB 에서 영구 삭제합니다.\n` +
      `되돌릴 수 없습니다. 진행할까요?`
    )) return;
    setOrphanBusy(true);
    try {
      const r = await window.api.gcOrphanCandidates({ dryRun: false, limit: 8 });
      toasts.push({
        kind: 'success',
        message: `삭제 완료 — ${r.deleted}건 row 제거 (스캔 ${r.scanned_total}, orphan ${r.orphans_found}).`,
      });
      setOrphanScan(null);
      dbStat.reload();
    } catch (e) {
      toasts.push({ kind: 'error', message: '삭제 실패: ' + (e.message || e) });
    } finally {
      setOrphanBusy(false);
    }
  }

  const appOk = health.data?.ok === true;
  const comfyuiOk = comfyuiHealth.data?.ok === true;
  // a1111 backend 가 살아있는지 — deprecated 칩 색깔에만 사용.
  const a1111Backend = healthSd.data?.backends?.a1111;
  const a1111Ok = a1111Backend?.ok === true;
  const gcState = gc.data || {};
  const gcResult = gcState.last_result || {};
  const dbSize = dbStat.data?.size_bytes;
  const queue = dbStat.data?.queue || {};

  return (
    <div>
      <window.PageToolbar
        left={
          <>
            <span className={`chip ${appOk ? '' : 'fail'}`}>app · {appOk ? 'OK' : 'DOWN'}</span>
            <span className={`chip ${comfyuiOk ? '' : 'fail'}`}>comfyui · {comfyuiOk ? 'OK' : 'DOWN'}</span>
            <span className="chip">gc · <b>{gcState.run_count ?? 0}</b> runs</span>
          </>
        }
        right={<button className="btn" onClick={() => { health.reload(); comfyuiHealth.reload(); healthSd.reload(); gc.reload(); }} title="새로고침">↻</button>}
        info={{
          title: 'system',
          text: 'app/ComfyUI 헬스체크, 후보 이미지 GC 상태, 수동 트리거. 8초마다 자동 폴링. A1111 backend 는 deprecated.',
        }}
      />

      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(4, 1fr)', marginBottom: 20 }}>
        <div className="panel-card">
          <h3>Health · App</h3>
          <div style={{ fontSize: 24, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            <span className={`pill ${appOk ? 'pill-ok' : 'pill-fail'}`}>{appOk ? 'OK' : 'DOWN'}</span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 8 }}>
            service: {health.data?.service || '—'}
          </p>
        </div>

        <div className="panel-card">
          <h3>Health · ComfyUI <span className="pill pill-ok" style={{ fontSize: 9, marginLeft: 6 }}>primary</span></h3>
          <div style={{ fontSize: 24, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            <span className={`pill ${comfyuiOk ? 'pill-ok' : 'pill-fail'}`}>
              {comfyuiOk ? 'OK' : (comfyuiHealth.data?.error ? 'ERROR' : 'CHECKING')}
            </span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 8, wordBreak: 'break-word' }}>
            {comfyuiHealth.data?.host || comfyuiHealth.data?.error || 'ComfyUI /system_stats + /queue'}
          </p>
          {comfyuiOk && comfyuiHealth.data && (
            <p style={{ color: 'var(--text-faint)', fontSize: 10, marginTop: 4, fontFamily: 'var(--font-mono)' }}>
              v{comfyuiHealth.data.comfyui_version || '?'} · queue {comfyuiHealth.data.queue?.running ?? 0}/{comfyuiHealth.data.queue?.pending ?? 0}
              {' · '}wf {comfyuiHealth.data.workflows_available ?? 0}
            </p>
          )}
        </div>

        <div className="panel-card">
          <h3>GC runs</h3>
          <div style={{ fontSize: 24, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            {gcState.run_count ?? 0}<span style={{ fontSize: 13, color: 'var(--text-muted)' }}> runs</span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 8 }}>
            마지막: {fmtTs(gcState.last_run_at)}
          </p>
        </div>
        <div className="panel-card">
          <h3>Disk/Queue</h3>
          <div style={{ fontSize: 24, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            {fmtBytes(dbSize)}
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 8 }}>
            q {queue.queued_total ?? 0} · run {queue.processing ?? 0} · fail {queue.failed ?? 0}
          </p>
        </div>
      </div>

      {/* A1111 deprecated backend — 작은 박스로 축소. */}
      <div className="panel-card" style={{
        padding: '8px 12px', marginBottom: 16,
        display: 'flex', alignItems: 'center', gap: 10,
        background: 'var(--bg-elev-1)',
        borderColor: 'var(--border-soft)',
      }}>
        <span className="pill" style={{ fontSize: 10 }}>deprecated</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
          a1111 backend · {a1111Ok ? 'OK' : (a1111Backend?.error || 'DOWN')}
        </span>
        <div style={{ flex: 1 }}/>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
          다음 메이저에서 제거 예정 (PLAN_comfyui_catalog.md §10)
        </span>
      </div>

      <div className="panel-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Orphan candidates (이미지 없는 후보 row)</h3>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="btn" onClick={scanOrphans} disabled={orphanBusy}>
              {orphanBusy ? '…' : '스캔 (dry-run)'}
            </button>
            {orphanScan && orphanScan.orphans_found > 0 && (
              <button type="button" className="btn"
                      onClick={deleteOrphans} disabled={orphanBusy}
                      style={{ background: 'var(--accent-reject)', color: '#fff', borderColor: 'var(--accent-reject)' }}>
                {orphanScan.orphans_found}건 영구 삭제
              </button>
            )}
          </div>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
          {orphanScan ? (
            <>
              스캔 {orphanScan.scanned_total} · orphan <b style={{ color: orphanScan.orphans_found > 0 ? 'var(--accent-warning, var(--accent-pick))' : 'var(--accent-approve)' }}>{orphanScan.orphans_found}</b>
              {orphanScan.deleted > 0 && <> · 직전 삭제 {orphanScan.deleted}</>}
            </>
          ) : (
            <span>모든 candidate row 의 image_path 가 disk 에 실재하는지 검사. dangling 만 정리 — 정상 row 는 건드리지 않음.</span>
          )}
        </div>
        {orphanScan && orphanScan.sample && orphanScan.sample.length > 0 && (
          <details>
            <summary style={{ fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer' }}>sample paths ({orphanScan.sample.length})</summary>
            <ul style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginTop: 6, listStyle: 'none', padding: 0 }}>
              {orphanScan.sample.map((s) => (
                <li key={s.id} style={{ wordBreak: 'break-all', marginBottom: 2 }}>
                  <span style={{ color: 'var(--text-muted)' }}>id={s.id}</span> {s.image_path}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      <div className="panel-card">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>후보 이미지 GC</h3>
          <button type="button" className="btn btn-primary" onClick={runGc} disabled={running}>
            {running ? '…' : 'GC 즉시 실행'}
          </button>
        </div>
        <div style={{ marginBottom: 10, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
          last delta · deleted {gcResult.deleted_files ?? 0} · scanned {gcResult.scanned_files ?? 0} · freed {fmtBytes(gcResult.freed_bytes)}
        </div>
        <dl className="meta-block">
          <dt>마지막 실행</dt><dd>{fmtTs(gcState.last_run_at)}</dd>
          <dt>삭제 파일</dt><dd>{gcResult.deleted_files ?? '—'}</dd>
          <dt>스캔 파일</dt><dd>{gcResult.scanned_files ?? '—'}</dd>
          <dt>회수 용량</dt><dd>{fmtBytes(gcResult.freed_bytes)}</dd>
          {gcState.last_error ? (
            <>
              <dt style={{ color: 'var(--accent-reject)' }}>마지막 에러</dt>
              <dd style={{ color: 'var(--accent-reject)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                {gcState.last_error}
              </dd>
            </>
          ) : null}
        </dl>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 20 }}>
        <DbBlock data={dbStat.data} loading={dbStat.loading} error={dbStat.error}/>
        <WorkerBlock data={worker.data} loading={worker.loading} error={worker.error}/>
      </div>

      <LogsBlock data={logs.data} loading={logs.loading} error={logs.error} reload={logs.reload}/>
    </div>
  );
}

function DbBlock({ data, loading, error }) {
  const rows = data?.tables || {};
  const queue = data?.queue || {};
  const size = data?.size_bytes;
  return (
    <div className="panel-card">
      <h3 style={{ margin: '0 0 10px' }}>DB</h3>
      {loading && !data && <window.Skeleton height={80}/>}
      {error && <div style={{ color: 'var(--accent-reject)' }}>{String(error.message || error)}</div>}
      {data && (
        <dl className="meta-block">
          <dt>db file size</dt><dd>{fmtBytes(size)}</dd>
          <dt>path</dt>
          <dd style={{ fontFamily: 'var(--font-mono)', fontSize: 11, wordBreak: 'break-all', color: 'var(--text-muted)' }}>{data.path}</dd>
          <dt>jobs</dt><dd>{rows.jobs ?? '—'}</dd>
          <dt>tasks</dt><dd>
            {rows.generation_tasks ?? '—'}
            {(queue.queued_total != null) && (
              <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>
                · q {queue.queued_total} (due {queue.queued_due}) · r {queue.processing} · f {queue.failed}
              </span>
            )}
          </dd>
          <dt>candidates</dt><dd>{rows.asset_candidates ?? '—'}</dd>
          <dt>assets</dt><dd>{rows.assets ?? '—'}</dd>
        </dl>
      )}
    </div>
  );
}

function WorkerBlock({ data, loading, error }) {
  if (loading && !data) return <div className="panel-card"><window.Skeleton height={80}/></div>;
  const alive = data?.alive === true;
  const cur = data?.current_task;
  return (
    <div className="panel-card">
      <h3 style={{ margin: '0 0 10px' }}>Worker</h3>
      {error && <div style={{ color: 'var(--accent-reject)' }}>{String(error.message || error)}</div>}
      {data && (
        <>
          <div>
            <span className={`pill ${alive ? 'pill-ok' : 'pill-fail'}`}>
              {alive ? 'ALIVE' : 'DOWN'}
            </span>
          </div>
          <dl className="meta-block" style={{ marginTop: 10 }}>
            <dt>last heartbeat</dt><dd>{fmtTs(data.last_heartbeat_at)}</dd>
            <dt>current task</dt>
            <dd style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
              {cur ? (
                <>
                  #{cur.id ?? cur.task_id ?? '—'}
                  {(cur.batch_id || cur.batch) && (
                    <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>
                      · {String(cur.batch_id || cur.batch).slice(0, 12)}…
                    </span>
                  )}
                </>
              ) : data.last_task_id ? (
                <span style={{ color: 'var(--text-faint)' }}>idle · last #{data.last_task_id}</span>
              ) : '—'}
            </dd>
            <dt>processed</dt><dd>{data.processed_count ?? 0}</dd>
            <dt>queue depth</dt>
            <dd>
              {data.queue_depth ?? 0}
              <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>
                · due {data.queue_due ?? 0} · run {data.processing ?? 0} · fail {data.failed ?? 0}
              </span>
            </dd>
          </dl>
        </>
      )}
    </div>
  );
}

function LogsBlock({ data, loading, error, reload }) {
  const [levelFilter, setLevelFilter] = useState('all');
  const items = data?.items || [];
  const rows = levelFilter === 'all' ? items : items.filter((l) => l.level === levelFilter);
  return (
    <div className="panel-card" style={{ marginTop: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>최근 로그 (warning / error)</h3>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className={`btn ${levelFilter === 'all' ? 'btn-primary' : ''}`} onClick={() => setLevelFilter('all')}>all</button>
          <button className={`btn ${levelFilter === 'warning' ? 'btn-primary' : ''}`} onClick={() => setLevelFilter('warning')}>warning</button>
          <button className={`btn ${levelFilter === 'error' ? 'btn-primary' : ''}`} onClick={() => setLevelFilter('error')}>error</button>
          <button className="btn" onClick={reload} title="새로고침">↻</button>
        </div>
      </div>
      {loading && !data && <window.Skeleton height={120}/>}
      {error && <div style={{ color: 'var(--accent-reject)' }}>{String(error.message || error)}</div>}
      {data && rows.length === 0 && (
        <div style={{ color: 'var(--text-muted)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
          최근 warning/error 없음 — 건강합니다.
        </div>
      )}
      {rows.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 320, overflow: 'auto' }}>
          {rows.map((l, i) => (
            <div
              key={i}
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                display: 'grid',
                gridTemplateColumns: '80px 70px 1fr',
                gap: 10,
                padding: '4px 0',
                borderBottom: '1px solid var(--border-subtle)',
              }}
            >
              <span style={{ color: 'var(--text-faint)' }}>{(l.ts || '').slice(11, 19)}</span>
              <span style={{ color: l.level === 'error' ? 'var(--accent-reject)' : 'var(--accent-warning)' }}>
                {l.level}
              </span>
              <span style={{ wordBreak: 'break-word' }}>{l.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

window.System = System;

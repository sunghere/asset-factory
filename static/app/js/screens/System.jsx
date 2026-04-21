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

  const health = window.useAsync(() => window.api.health(), []);
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
    health.reload(); healthSd.reload(); gc.reload();
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

  const appOk = health.data?.ok === true;
  const sdOk = healthSd.data?.ok === true;
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
            <span className={`chip ${sdOk ? '' : 'fail'}`}>sd · {sdOk ? 'OK' : 'DOWN'}</span>
            <span className="chip">gc · <b>{gcState.run_count ?? 0}</b> runs</span>
          </>
        }
        right={<button className="btn" onClick={() => { health.reload(); healthSd.reload(); gc.reload(); }} title="새로고침">↻</button>}
        info={{
          title: 'system',
          text: 'app/SD 헬스체크, 후보 이미지 GC 상태, 수동 트리거. 8초마다 자동 폴링. 조용히 성공하고 에러만 시끄럽게 알립니다.',
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
          <h3>Health · SD</h3>
          <div style={{ fontSize: 24, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            <span className={`pill ${sdOk ? 'pill-ok' : 'pill-fail'}`}>
              {sdOk ? 'OK' : (healthSd.data?.error ? 'ERROR' : 'CHECKING')}
            </span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 8, wordBreak: 'break-word' }}>
            {healthSd.data?.base_url || healthSd.data?.error || 'A1111 /sdapi/v1/options'}
          </p>
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

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

  // Light-weight polling — we don't care about drift, we just want fresh values.
  window.useInterval(() => {
    health.reload(); healthSd.reload(); gc.reload();
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

      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(3, 1fr)', marginBottom: 20 }}>
        <div className="panel-card">
          <h3>앱 헬스</h3>
          <div style={{ fontSize: 24, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            <span className={`pill ${appOk ? 'pill-ok' : 'pill-fail'}`}>{appOk ? 'OK' : 'DOWN'}</span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 8 }}>
            service: {health.data?.service || '—'}
          </p>
        </div>

        <div className="panel-card">
          <h3>SD 서버</h3>
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
          <h3>GC 누적</h3>
          <div style={{ fontSize: 24, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
            {gcState.run_count ?? 0}<span style={{ fontSize: 13, color: 'var(--text-muted)' }}> runs</span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 8 }}>
            마지막: {fmtTs(gcState.last_run_at)}
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
    </div>
  );
}

window.System = System;

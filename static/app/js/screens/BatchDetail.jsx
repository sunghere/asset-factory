/* BatchDetail — inspect a single design batch.
   Tabs (spec §6.6):
     - tasks       : per-task status / attempts / last_error + "retry failed"
     - candidates  : grid of all candidate images with reject badge
     - spec        : aggregated spec for the whole batch */

const { useState: useStateBD } = React;

function BatchDetail({ batchId }) {
  const [tab, setTab] = useStateBD('tasks');
  const toasts = window.useToasts();

  const candidates = window.useAsync(
    () => window.api.listBatchCandidates(batchId),
    [batchId],
  );
  const detail = window.useAsync(
    () => window.api.getBatchDetail(batchId),
    [batchId],
  );
  const tasks = window.useAsync(
    () => window.api.listBatchTasks(batchId),
    [batchId],
  );

  // 실시간 업데이트 — 이 배치와 관련된 모든 이벤트에 세 소스 중 영향 받는 것 reload.
  window.useSSE((events) => {
    let refreshCands = false, refreshTasks = false, refreshDetail = false;
    for (const e of events) {
      if (e.batch_id && e.batch_id !== batchId) continue;
      if (['candidate_added', 'candidate_rejected', 'candidate_unrejected',
        'validation_updated'].includes(e.type)) refreshCands = true;
      if (['task_done', 'task_error', 'batch_retry_failed',
        'batch_regenerate_failed_queued', 'batch_revalidate_failed_done'].includes(e.type)) {
        refreshTasks = true; refreshDetail = true;
      }
    }
    if (refreshCands) candidates.reload();
    if (refreshTasks) tasks.reload();
    if (refreshDetail) detail.reload();
  });

  const batchRow = detail.data;
  const items = candidates.data?.items || [];
  const rejectedCount = items.filter((c) => c.is_rejected).length;
  const activeCount = items.length - rejectedCount;
  const taskRows = tasks.data?.items || [];
  const failedTaskCount = taskRows.filter((t) => t.status === 'failed').length;

  async function onRetryFailed() {
    if (failedTaskCount === 0) return;
    try {
      const resp = await window.api.retryFailedTasks(batchId);
      toasts.push({
        kind: 'success',
        message: `실패 ${resp.retried_count ?? failedTaskCount} 건 재큐잉`,
      });
      tasks.reload();
      detail.reload();
    } catch (e) {
      toasts.push({ kind: 'error', message: 'retry 실패: ' + (e.message || e) });
    }
  }

  async function onReject(cand) {
    // Optimistic feedback + undo — same pattern as cherry-pick.
    try {
      await window.api.rejectCandidate(batchId, cand.id);
      candidates.reload();
      toasts.push({
        kind: 'info',
        message: `후보 ${String(cand.id).slice(-6)} 거부됨`,
        onUndo: async () => {
          await window.api.unrejectCandidate(batchId, cand.id);
          candidates.reload();
        },
      });
    } catch (e) {
      toasts.push({ kind: 'error', message: 'reject 실패: ' + (e.message || e) });
    }
  }

  return (
    <div>
      <div className="screen-header">
        <div>
          <div className="eyebrow">
            <a href="/app/batches" onClick={(e) => { e.preventDefault(); window.navigate('/batches'); }}
               style={{ color: 'var(--text-muted)' }}>← batches</a>
            {' / '}{batchRow?.project || '—'}
          </div>
          <h1>
            {batchRow?.asset_key || batchId}
            <span className="hint" style={{ marginLeft: 10, fontFamily: 'var(--font-mono)' }}>{batchId}</span>
          </h1>
        </div>
        {batchRow && (
          <a
            className="btn btn-primary"
            href={`/app/cherry-pick/${batchId}`}
            onClick={(e) => { e.preventDefault(); window.navigate(`/cherry-pick/${batchId}`); }}
          >▶ cherry-pick 열기</a>
        )}
      </div>

      {batchRow && (
        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(5, 1fr)', marginBottom: 16 }}>
          <StatCard label="총 작업" value={batchRow.tasks?.total}/>
          <StatCard label="진행" value={batchRow.tasks?.active} tone={batchRow.tasks?.active > 0 ? 'warn' : null}/>
          <StatCard label="실패" value={batchRow.tasks?.failed} tone={batchRow.tasks?.failed > 0 ? 'fail' : null}/>
          <StatCard label="후보" value={items.length} sub={`활성 ${activeCount} · 거부 ${rejectedCount}`}/>
          <StatCard label="최초 생성" value={(batchRow.first_created_at || '').slice(5, 16).replace('T', ' ')} mono/>
        </div>
      )}

      <div className="tabs">
        <button className={`tab ${tab === 'tasks' ? 'active' : ''}`} onClick={() => setTab('tasks')}>
          /tasks <span style={{ color: 'var(--text-faint)' }}>({taskRows.length})</span>
          {failedTaskCount > 0 && (
            <span className="pill pill-fail" style={{ marginLeft: 6 }}>fail {failedTaskCount}</span>
          )}
        </button>
        <button className={`tab ${tab === 'candidates' ? 'active' : ''}`} onClick={() => setTab('candidates')}>
          /candidates <span style={{ color: 'var(--text-faint)' }}>({items.length})</span>
        </button>
        <button className={`tab ${tab === 'spec' ? 'active' : ''}`} onClick={() => setTab('spec')}>
          /spec
        </button>
      </div>

      {tab === 'tasks' && (
        <TasksView
          tasks={tasks}
          failedCount={failedTaskCount}
          onRetryFailed={onRetryFailed}
        />
      )}

      {candidates.loading && !candidates.data && <window.Skeleton height={200}/>}
      {candidates.error && (
        <div className="error-banner"><span>⚠</span><span>{String(candidates.error.message || candidates.error)}</span></div>
      )}

      {!candidates.loading && !candidates.error && tab === 'candidates' && (
        items.length === 0 ? <window.EmptyState title="후보 없음" hint="아직 생성 중이거나 배치가 비어있습니다."/> : (
          <div className="asset-grid">
            {items.map((c) => (
              <div
                key={c.id}
                className={`asset-card ${c.is_rejected ? '' : ''}`}
                style={c.is_rejected ? { opacity: 0.4 } : undefined}
                onDoubleClick={() => !c.is_rejected && onReject(c)}
                title={c.is_rejected ? 'rejected (double-click disabled)' : 'double-click to reject'}
              >
                <div className="thumb-box">
                  <img src={c.image_url} alt={`cand-${c.id}`} loading="lazy"/>
                  {c.is_rejected && <span className="vbdg fail">REJ</span>}
                  {!c.is_rejected && c.validation_status === 'fail' && (
                    <span
                      className="vbdg fail"
                      title={c.validation_message || 'validation=fail'}
                      style={{ cursor: 'help', top: 4, left: 'auto', right: 4 }}
                    >!</span>
                  )}
                </div>
                <div className="strip">
                  <span className="asset-key">#{String(c.id).slice(-6)}</span>
                  <span className="meta">slot {c.slot_index}</span>
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {tab === 'spec' && (
        detail.loading && !detail.data ? <window.Skeleton height={220}/> :
        detail.error ? (
          <div className="error-banner"><span>⚠</span><span>{String(detail.error.message || detail.error)}</span></div>
        ) : !detail.data ? (
          <window.EmptyState title="spec 데이터 없음" hint="이 배치의 태스크가 아직 없습니다."/>
        ) : <SpecView detail={detail.data}/>
      )}
    </div>
  );
}

function StatCard({ label, value, sub, mono, tone }) {
  const color = tone === 'warn' ? 'var(--accent-warn, var(--accent-pick))'
    : tone === 'fail' ? 'var(--accent-reject)'
    : 'var(--text-primary)';
  return (
    <div className="panel-card" style={{ padding: '14px 16px' }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600, fontFamily: mono ? 'var(--font-mono)' : 'inherit', color }}>
        {value ?? '—'}
      </div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function SpecView({ detail }) {
  // detail is the /api/batches/{id} payload — see models.get_batch_detail.
  // We render three blocks: header(id/project/...), axes(seeds/models/prompts/
  // loras distinct values = expansion matrix), and common(steps/cfg/...).
  const spec = detail.spec || {};
  const common = spec.common || {};
  const cand = detail.candidates || {};
  const val = cand.validation || {};

  const headerRows = [
    ['project',         detail.project],
    ['category',        detail.category],
    ['asset_key',       detail.asset_key],
    ['batch_id',        detail.batch_id],
    ['job_id',          detail.job_id],
    ['first_created',   detail.first_created_at],
    ['last_updated',    detail.last_updated_at],
  ];

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div className="panel-card">
        <h3 style={{ margin: '0 0 8px' }}>meta</h3>
        <dl className="meta-block">
          {headerRows.map(([k, v]) => (
            <React.Fragment key={k}>
              <dt>{k}</dt>
              <dd>{renderValue(v)}</dd>
            </React.Fragment>
          ))}
        </dl>
      </div>

      <div className="panel-card">
        <h3 style={{ margin: '0 0 8px' }}>
          axes
          <span className="hint" style={{ marginLeft: 8 }}>
            task_count={spec.task_count ?? '—'}
          </span>
        </h3>
        <Axis label="seeds"   values={spec.seeds}/>
        <Axis label="models"  values={spec.models}/>
        <Axis label="prompts" values={spec.prompts} pre/>
        <Axis label="neg_prompts" values={spec.negative_prompts} pre/>
        <Axis label="loras"   values={parseLoras(spec.loras)} pre/>
      </div>

      <div className="panel-card">
        <h3 style={{ margin: '0 0 8px' }}>common params</h3>
        <dl className="meta-block">
          {Object.entries(common).map(([k, v]) => (
            <React.Fragment key={k}>
              <dt>{k}</dt>
              <dd>
                {renderValue(v?.value)}
                {v && v.uniform === false && (
                  <span
                    className="bdg warn"
                    title="배치 내 태스크마다 값이 다름"
                    style={{ marginLeft: 6, position: 'static' }}
                  >mixed</span>
                )}
              </dd>
            </React.Fragment>
          ))}
        </dl>
      </div>

      <div className="panel-card">
        <h3 style={{ margin: '0 0 8px' }}>candidates</h3>
        <div style={{ display: 'grid', gap: 6, gridTemplateColumns: 'repeat(5, 1fr)' }}>
          <Stat label="total"    value={cand.total}/>
          <Stat label="picked"   value={cand.picked}/>
          <Stat label="rejected" value={cand.rejected}/>
          <Stat label="val·pass" value={val.pass}/>
          <Stat label="val·fail" value={val.fail} tone={val.fail ? 'fail' : null}/>
        </div>
      </div>
    </div>
  );
}

function Axis({ label, values, pre }) {
  const arr = Array.isArray(values) ? values : [];
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        {label} <span style={{ color: 'var(--text-faint)' }}>× {arr.length || 0}</span>
      </div>
      {arr.length === 0 ? (
        <div style={{ color: 'var(--text-faint)' }}>—</div>
      ) : (
        <ul style={{ margin: '4px 0 0', paddingLeft: 18, display: 'grid', gap: 2 }}>
          {arr.map((v, i) => (
            <li key={i} style={{ fontFamily: pre ? 'var(--font-mono)' : 'inherit', fontSize: pre ? 12 : 13 }}>
              {v == null || v === '' ? <span style={{ color: 'var(--text-faint)' }}>—</span> : String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value, tone }) {
  const color = tone === 'fail' ? 'var(--accent-reject)' : 'var(--text-primary)';
  return (
    <div style={{ padding: '6px 10px', background: 'var(--bg-subtle, transparent)', border: '1px solid var(--border-subtle)', borderRadius: 4 }}>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color, fontFamily: 'var(--font-mono)' }}>{value ?? '—'}</div>
    </div>
  );
}

function parseLoras(raw) {
  // DB 는 lora_spec 을 JSON 문자열로 저장한다. distinct 결과도 JSON 문자열 배열.
  if (!Array.isArray(raw)) return [];
  return raw.map((v) => {
    if (v == null) return null;
    try {
      const parsed = JSON.parse(v);
      if (Array.isArray(parsed) && parsed.length === 0) return '[] (no lora)';
      return JSON.stringify(parsed);
    } catch {
      return String(v);
    }
  });
}

function renderValue(v) {
  if (v == null || v === '') return <span style={{ color: 'var(--text-faint)' }}>—</span>;
  if (typeof v === 'object') return <pre style={{ margin: 0, fontSize: 11 }}>{JSON.stringify(v, null, 2)}</pre>;
  return String(v);
}

function TasksView({ tasks, failedCount, onRetryFailed }) {
  if (tasks.loading && !tasks.data) return <window.Skeleton height={200}/>;
  if (tasks.error) {
    return <div className="error-banner">
      <span>⚠</span><span>{String(tasks.error.message || tasks.error)}</span>
    </div>;
  }
  const rows = tasks.data?.items || [];
  if (rows.length === 0) {
    return <window.EmptyState title="태스크 없음" hint="이 배치에는 아직 태스크가 없습니다."/>;
  }

  const byStatus = { queued: 0, running: 0, done: 0, failed: 0 };
  for (const t of rows) byStatus[t.status] = (byStatus[t.status] || 0) + 1;

  return (
    <div>
      <div style={{
        display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10,
        fontFamily: 'var(--font-mono)', fontSize: 12,
      }}>
        <span className="pill">queued <b style={{ marginLeft: 4 }}>{byStatus.queued || 0}</b></span>
        <span className="pill pill-warn">running <b style={{ marginLeft: 4 }}>{byStatus.running || 0}</b></span>
        <span className="pill pill-ok">done <b style={{ marginLeft: 4 }}>{byStatus.done || 0}</b></span>
        <span className="pill pill-fail">failed <b style={{ marginLeft: 4 }}>{byStatus.failed || 0}</b></span>
        <div style={{ flex: 1 }}/>
        <button
          className="btn btn-primary"
          disabled={failedCount === 0}
          onClick={onRetryFailed}
          title={failedCount ? `${failedCount} 건 재큐잉` : '실패 태스크 없음'}
        >↻ 실패만 재생성 ({failedCount})</button>
      </div>

      <div className="panel-card" style={{ padding: 0, overflow: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 60 }}>id</th>
              <th style={{ width: 100 }}>status</th>
              <th>model</th>
              <th style={{ width: 100 }}>seed</th>
              <th style={{ width: 100 }}>attempts</th>
              <th>last_error</th>
              <th style={{ width: 150 }}>updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className={t.status === 'failed' ? 'row-fail' : undefined}>
                <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{t.id}</td>
                <td><TaskStatusPill status={t.status}/></td>
                <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12, wordBreak: 'break-all' }}>
                  {t.model || '—'}
                </td>
                <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
                  {t.seed ?? '—'}
                </td>
                <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                  {t.attempts ?? 0}{t.max_retries ? <span style={{ color: 'var(--text-faint)' }}> / {t.max_retries}</span> : null}
                </td>
                <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-reject)' }}>
                  {t.last_error ? (
                    <details>
                      <summary style={{ cursor: 'pointer' }}>
                        {String(t.last_error).slice(0, 60)}
                        {String(t.last_error).length > 60 ? '…' : ''}
                      </summary>
                      <pre style={{
                        whiteSpace: 'pre-wrap', marginTop: 4, padding: 6,
                        background: 'var(--bg-recess)', borderRadius: 4,
                        color: 'var(--text-primary)',
                      }}>{t.last_error}</pre>
                    </details>
                  ) : '—'}
                </td>
                <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                  {(t.updated_at || t.created_at || '').slice(0, 19).replace('T', ' ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TaskStatusPill({ status }) {
  const cls = status === 'done' ? 'pill-ok'
    : status === 'running' ? 'pill-warn'
    : status === 'failed' ? 'pill-fail'
    : '';
  return <span className={`pill ${cls}`}>{status || '—'}</span>;
}

window.BatchDetail = BatchDetail;

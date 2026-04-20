/* BatchDetail — inspect a single design batch.
   Tabs:
     - candidates  : grid of all candidate images with reject badge
     - spec        : aggregated spec for the whole batch, sourced from
                     GET /api/batches/{id} (generation_tasks distinct values)
   "Tasks" tab from the spec doc is not rendered separately because the
   candidates tab already shows every task's output; the spec tab shows the
   input side. */

const { useState } = React;

function BatchDetail({ batchId }) {
  const [tab, setTab] = useState('candidates');
  const toasts = window.useToasts();

  const candidates = window.useAsync(
    () => window.api.listBatchCandidates(batchId),
    [batchId],
  );
  const detail = window.useAsync(
    () => window.api.getBatchDetail(batchId),
    [batchId],
  );

  const batchRow = detail.data;
  const items = candidates.data?.items || [];
  const rejectedCount = items.filter((c) => c.is_rejected).length;
  const activeCount = items.length - rejectedCount;

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
        <button className={`tab ${tab === 'candidates' ? 'active' : ''}`} onClick={() => setTab('candidates')}>
          /candidates <span style={{ color: 'var(--text-faint)' }}>({items.length})</span>
        </button>
        <button className={`tab ${tab === 'spec' ? 'active' : ''}`} onClick={() => setTab('spec')}>
          /spec
        </button>
      </div>

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

window.BatchDetail = BatchDetail;

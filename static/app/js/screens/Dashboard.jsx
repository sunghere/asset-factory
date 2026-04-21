/* Dashboard — project-grouped today board.
   Sources: /api/cherry-pick/queue (per-batch pending), /api/jobs/recent,
   /api/assets/summary, /api/health, /api/health/sd. */

const { useMemo } = React;

function ProjectColor(name) {
  const palette = ['var(--accent-approve)', 'var(--accent-pick)', 'var(--accent-success)', 'var(--accent-warning)'];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return palette[Math.abs(h) % palette.length];
}

function Dashboard() {
  const queue = window.useAsync(() => window.api.cherryPickQueue({ limit: 200 }), []);
  const summary = window.useAsync(() => window.api.assetSummary(), []);
  const recentJobs = window.useAsync(() => window.api.recentJobs(8), []);
  const healthSd = window.useAsync(
    () => window.api.healthSd().catch((err) => ({ ok: false, error: String(err?.body?.detail || err?.message || err) })),
    [],
  );
  // SD health 는 SSE 로 안 날아오니 천천히 폴링한다 (UI 의 "언제 확인?" 에 답).
  window.useInterval?.(() => { healthSd.reload(); }, 20000);

  // SSE — 실시간 큐/써머리 업데이트. 폴링 제거.
  window.useSSE((batch) => {
    let needQueue = false, needSummary = false, needJobs = false;
    for (const e of batch) {
      if (['candidate_added', 'candidate_rejected', 'candidate_unrejected',
        'task_done', 'batch_job_created', 'design_batch_created',
        'batch_retry_failed', 'batch_regenerate_failed_queued'].includes(e.type)) needQueue = true;
      if (['asset_approved_from_candidate', 'asset_status_changed',
        'asset_approve_undone', 'validation_updated'].includes(e.type)) {
        needSummary = true; needQueue = true;
      }
      if (['job_created', 'task_done', 'task_error',
        'batch_job_created', 'design_batch_created',
        'scan_completed', 'export_completed'].includes(e.type)) needJobs = true;
    }
    if (needQueue) queue.reload();
    if (needSummary) summary.reload();
    if (needJobs) recentJobs.reload();
  });

  // Group batches by project so each row is a "today board" for one project.
  const projects = useMemo(() => {
    const items = queue.data?.items || [];
    const buckets = new Map();
    for (const b of items) {
      const key = b.project || 'default';
      if (!buckets.has(key)) buckets.set(key, { name: key, batches: [], remaining: 0, total: 0, done: 0 });
      const bucket = buckets.get(key);
      bucket.batches.push(b);
      bucket.remaining += Number(b.remaining || 0);
      bucket.total += Number(b.total || 0);
      bucket.done += Number(b.total || 0) - Number(b.remaining || 0);
    }
    return Array.from(buckets.values()).sort((a, b) => b.remaining - a.remaining);
  }, [queue.data]);

  const firstPending = useMemo(() => {
    const items = (queue.data?.items || []).filter((b) => !b.approved && b.remaining > 0);
    return items[0] || null;
  }, [queue.data]);

  // ⏎ jumps into the first pending batch — the "1-key into work" promise.
  window.useKeyboard({
    Enter: () => firstPending && window.navigate(`/cherry-pick/${firstPending.batch_id}`),
  }, [firstPending]);

  const totalRemaining = queue.data?.total_remaining ?? null;
  const totalBatches = queue.data?.pending_batches ?? null;

  return (
    <div>
      <window.PageToolbar
        left={
          <>
            <span className="chip">
              <b style={{ color: 'var(--accent-approve)' }}>{totalBatches ?? '—'}</b>
              <span style={{ opacity: 0.6 }}>batches</span>
            </span>
            <span className="chip">
              <b>{totalRemaining ?? '—'}</b>
              <span style={{ opacity: 0.6 }}>장 남음</span>
            </span>
          </>
        }
        right={
          <button
            className="btn btn-primary"
            disabled={!firstPending}
            onClick={() => window.navigate('/queue')}
          >
            ▶ 체리픽 시작 (Enter)
          </button>
        }
        info={{
          title: 'dashboard',
          text: '오늘 처리해야 하는 cherry-pick 큐를 프로젝트별로 묶어 보여줍니다. Enter 키 = 가장 오래된 pending 배치로 바로 진입.',
        }}
      />

      <window.ErrorPanel error={queue.error} onRetry={queue.reload}/>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {queue.loading && !queue.data && (
          <>
            <window.Skeleton height={120}/>
            <window.Skeleton height={120}/>
          </>
        )}
        {queue.data && projects.length === 0 && (
          <window.EmptyState
            glyph="∅"
            title="오늘 처리할 batch 없음"
            hint="첫 batch를 보내려면 아래 curl cheatsheet를 실행하거나 수동 배치 생성으로 시작하세요."
            action={<button className="btn" onClick={() => window.navigate('/batches/new')}>+ 새 batch</button>}
          />
        )}
        {projects.map((p) => (
          <ProjectRow key={p.name} project={p}/>
        ))}
      </div>

      <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <Stat label="TOTAL" value={summary.data?.total ?? '—'} to="/assets"/>
        <Stat
          label="APPROVED"
          value={summary.data?.by_status?.approved ?? 0}
          accent="var(--accent-approve)"
          to="/assets?s=approved"
        />
        <Stat label="PENDING" value={summary.data?.by_status?.pending ?? 0} to="/assets?s=pending"/>
        <Stat
          label="FAILED · 검증"
          value={summary.data?.by_validation?.fail ?? 0}
          accent={(summary.data?.by_validation?.fail || 0) > 0 ? 'var(--accent-warning)' : undefined}
          to="/batches?status=failed"
        />
      </div>

      <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <SdHealthCard data={healthSd.data} loading={healthSd.loading} reload={healthSd.reload} onOpenSystem={() => window.navigate('/system')}/>
        <CategoryBarChart data={summary.data} loading={summary.loading}/>
      </div>

      <CurlCheatsheet/>

      <div className="panel-card" style={{ marginTop: 20 }}>
        <h3>RECENT JOBS</h3>
        {recentJobs.loading && <window.Skeleton height={48}/>}
        {recentJobs.data && recentJobs.data.length === 0 && (
          <div style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            최근 작업 없음
          </div>
        )}
        {recentJobs.data && recentJobs.data.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {recentJobs.data.map((j) => (
              <div
                key={j.id}
                className={`activity-row ${getRecentJobTarget(j) ? 'row-link' : ''}`}
                onClick={() => {
                  const to = getRecentJobTarget(j);
                  if (to) window.navigate(to);
                }}
              >
                <span className="t">{(j.created_at || '').slice(11, 19)}</span>
                <span className="host">{j.job_type}</span>
                <span className="task">{j.id}</span>
                <span className={`status ${j.status === 'completed' ? 'done' : j.status === 'failed' ? 'fail' : ''}`}>
                  {j.completed_count ?? 0}/{j.total_count ?? '?'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function getRecentJobTarget(job) {
  if (!job || typeof job !== 'object') return null;
  if (job.asset_id) return `/assets/${job.asset_id}`;
  if (job.batch_id) return `/batches/${job.batch_id}`;
  if (job.job_type === 'design_batch' || job.job_type === 'generate_batch') {
    return `/batches/${job.id}`;
  }
  return null;
}

function ProjectRow({ project }) {
  const remainingByBatch = useMemo(() => {
    return project.batches
      .filter((b) => !b.approved && b.remaining > 0)
      .sort((a, b) => new Date(a.first_created_at) - new Date(b.first_created_at));
  }, [project.batches]);
  const firstPending = remainingByBatch[0];

  return (
    <div className="panel-card" style={{
      display: 'grid', gridTemplateColumns: '220px 1fr 280px', gap: 20, alignItems: 'center', padding: 18,
    }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: ProjectColor(project.name) }}/>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 600 }}>{project.name}</span>
        </div>
        <div style={{ marginTop: 10, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7 }}>
          <div>큐 <b style={{ color: 'var(--accent-approve)' }}>{remainingByBatch.length}</b> batches</div>
          <div>남음 <b style={{ color: 'var(--text-primary)' }}>{project.remaining}</b>장</div>
          <div>총 후보 {project.total}장 · 진행 {project.done}장</div>
        </div>
      </div>

      <div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
          batches
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {project.batches.slice(0, 4).map((b) => (
            <window.Link
              key={b.batch_id}
              to={`/cherry-pick/${b.batch_id}`}
              style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 12, padding: '2px 0' }}
            >
              <span style={{ color: 'var(--text-secondary)' }}>
                {b.approved ? '✓ ' : '◐ '}{b.asset_key}
              </span>
              <span style={{ color: 'var(--text-muted)' }}>{b.remaining}/{b.total}</span>
            </window.Link>
          ))}
          {project.batches.length > 4 && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
              … +{project.batches.length - 4} more
            </span>
          )}
        </div>
      </div>

      <div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
          진행
        </div>
        <window.SegProgress approved={project.done} rejected={0} total={project.total}/>
        <div style={{ display: 'flex', gap: 6, marginTop: 12 }}>
          <button
            className="btn primary"
            style={{ flex: 1 }}
            disabled={!firstPending}
            onClick={() => firstPending && window.navigate(`/cherry-pick/${firstPending.batch_id}`)}
          >▶ 작업</button>
          <button
            className="btn"
            onClick={() => window.navigate('/queue')}
          >상세</button>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, accent, to }) {
  const clickable = !!to;
  return (
    <div
      className={`panel-card ${clickable ? 'row-link' : ''}`}
      onClick={() => clickable && window.navigate(to)}
      style={{ padding: 14, cursor: clickable ? 'pointer' : undefined }}
    >
      <div style={{
        fontSize: 10, color: 'var(--text-muted)',
        fontFamily: 'var(--font-mono)', letterSpacing: '0.1em',
      }}>{label}</div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 26, fontWeight: 600,
        color: accent || 'var(--text-primary)',
      }}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </div>
    </div>
  );
}

function SdHealthCard({ data, loading, reload, onOpenSystem }) {
  const ok = data?.ok === true;
  const err = data?.error;
  const state = loading && !data ? 'checking' : (ok ? 'ok' : (err ? 'error' : 'checking'));
  const color = state === 'ok' ? 'var(--accent-success)' : state === 'error' ? 'var(--accent-warning)' : 'var(--text-muted)';
  const label = state === 'ok' ? 'OK' : state === 'error' ? 'ERROR' : 'CHECKING';
  return (
    <div className="panel-card row-link" style={{ padding: 16 }} onClick={onOpenSystem}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>SD · A1111</h3>
        <button
          className="btn"
          onClick={(e) => {
            e.stopPropagation();
            reload && reload();
          }}
          title="재확인"
        >↻</button>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: color }}/>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 600, color }}>{label}</span>
        {ok && typeof data.model_count === 'number' && (
          <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            · {data.model_count} models
          </span>
        )}
      </div>
      <div style={{ marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', wordBreak: 'break-all' }}>
        {ok
          ? (Array.isArray(data.models) && data.models.length > 0 ? data.models.slice(0, 3).join(', ') + (data.models.length > 3 ? ' …' : '') : 'A1111 /sdapi/v1/sd-models')
          : (err || 'A1111 서버 응답 대기 중')}
      </div>
    </div>
  );
}

function CategoryBarChart({ data, loading }) {
  const entries = useMemo(() => {
    const map = data?.by_category || {};
    return Object.entries(map)
      .filter(([name]) => name && name !== 'null')
      .map(([name, count]) => ({ name, count: Number(count || 0) }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);
  }, [data]);
  const max = entries.reduce((m, e) => Math.max(m, e.count), 0) || 1;
  return (
    <div className="panel-card" style={{ padding: 16 }}>
      <h3 style={{ margin: '0 0 10px' }}>by_category</h3>
      {loading && !data && <window.Skeleton height={120}/>}
      {data && entries.length === 0 && (
        <div style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          카테고리 데이터 없음
        </div>
      )}
      {entries.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {entries.map((e) => (
            <div key={e.name} style={{ display: 'grid', gridTemplateColumns: '120px 1fr 48px', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={e.name}>
                {e.name}
              </span>
              <div style={{ height: 10, background: 'var(--bg-elev-2, #1a1a1a)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{
                  width: `${(e.count / max) * 100}%`, height: '100%',
                  background: 'var(--accent-approve)', transition: 'width 200ms ease',
                }}/>
              </div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', textAlign: 'right' }}>
                {e.count.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CurlCheatsheet() {
  const [copied, setCopied] = React.useState('');
  const base = (typeof window !== 'undefined' && window.location ? window.location.origin : 'http://localhost:8080');
  const apiKey = (typeof localStorage !== 'undefined' ? (localStorage.getItem('af_api_key') || localStorage.getItem('assetFactoryApiKey') || '') : '');
  const authHeader = apiKey ? ` -H 'X-API-Key: ${apiKey.slice(0, 6)}…'` : " -H 'X-API-Key: $AF_API_KEY'";
  const examples = [
    {
      id: 'health',
      label: 'health / SD 체크',
      cmd: `curl -s ${base}/api/health && echo && curl -s ${base}/api/health/sd`,
    },
    {
      id: 'summary',
      label: 'assets summary',
      cmd: `curl -s '${base}/api/assets/summary'`,
    },
    {
      id: 'queue',
      label: 'cherry-pick queue',
      cmd: `curl -s '${base}/api/cherry-pick/queue?limit=50'`,
    },
    {
      id: 'batch',
      label: '새 배치 생성 (POST)',
      cmd: `curl -s -X POST ${base}/api/batches${authHeader} \\
  -H 'Content-Type: application/json' \\
  -d '{"project":"default","asset_key":"cat_raising_bg","count":12}'`,
    },
  ];
  const doCopy = async (ex) => {
    try {
      await navigator.clipboard.writeText(ex.cmd);
      setCopied(ex.id);
      setTimeout(() => setCopied(''), 1200);
    } catch (_) { /* noop */ }
  };
  return (
    <div className="panel-card" style={{ marginTop: 14, padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>API · curl cheatsheet</h3>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
          {apiKey ? 'X-API-Key 감지됨 (앞 6자리만 표시)' : '인증이 필요한 엔드포인트는 AF_API_KEY 환경변수 사용'}
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {examples.map((ex) => (
          <div key={ex.id} style={{ border: '1px solid var(--border-subtle)', borderRadius: 4 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', borderBottom: '1px solid var(--border-subtle)' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>{ex.label}</span>
              <button className="btn" onClick={() => doCopy(ex)} title="복사">
                {copied === ex.id ? '✓ copied' : 'copy'}
              </button>
            </div>
            <pre style={{
              margin: 0, padding: 10, fontFamily: 'var(--font-mono)', fontSize: 11,
              color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              background: 'transparent',
            }}>{ex.cmd}</pre>
          </div>
        ))}
      </div>
    </div>
  );
}

window.Dashboard = Dashboard;

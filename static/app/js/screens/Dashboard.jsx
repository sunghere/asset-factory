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
            className="btn primary"
            disabled={!firstPending}
            onClick={() => firstPending && window.navigate(`/cherry-pick/${firstPending.batch_id}`)}
          >
            ▶ 체리픽 시작 (Enter)
          </button>
        }
        info={{
          title: 'dashboard',
          text: '오늘 처리해야 하는 cherry-pick 큐를 프로젝트별로 묶어 보여줍니다. Enter 키 = 가장 오래된 pending 배치로 바로 진입.',
        }}
      />

      {queue.error && <div className="error-banner" style={{ marginBottom: 16 }}>
        <span>⚠</span><span>큐를 불러오지 못했습니다 · {String(queue.error.message || queue.error)}</span>
      </div>}

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
            hint="POST /api/batches 로 새 batch를 등록하세요."
            action={<button className="btn" onClick={() => window.navigate('/regen')}>+ 재생성으로 이동</button>}
          />
        )}
        {projects.map((p) => (
          <ProjectRow key={p.name} project={p}/>
        ))}
      </div>

      <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <Stat label="TOTAL" value={summary.data?.total ?? '—'}/>
        <Stat
          label="APPROVED"
          value={summary.data?.by_status?.approved ?? 0}
          accent="var(--accent-approve)"
        />
        <Stat label="PENDING" value={summary.data?.by_status?.pending ?? 0}/>
        <Stat
          label="FAILED · 검증"
          value={summary.data?.by_validation?.fail ?? 0}
          accent={(summary.data?.by_validation?.fail || 0) > 0 ? 'var(--accent-warning)' : undefined}
        />
      </div>

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
              <div key={j.id} className="activity-row">
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

function Stat({ label, value, accent }) {
  return (
    <div className="panel-card" style={{ padding: 14 }}>
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

window.Dashboard = Dashboard;

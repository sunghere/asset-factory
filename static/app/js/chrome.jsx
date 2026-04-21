/* App chrome — Monogram, AppSideNav, AppTopBar, Kbd, SegProgress.
   Slide-frame code from the prototype is intentionally stripped. */

const { useEffect, useState } = React;

function Monogram({ size = 20 }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'block' }}>
        <rect x="0" y="0" width="20" height="20" rx="4" fill="none" stroke="#f5d76e" strokeWidth="1.5"/>
        <rect x="5" y="5" width="4" height="4" fill="#f5d76e"/>
        <rect x="11" y="5" width="4" height="4" fill="#8fb8ff"/>
        <rect x="5" y="11" width="4" height="4" fill="#8fb8ff"/>
        <rect x="11" y="11" width="4" height="4" fill="#f5d76e"/>
      </svg>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 11,
        color: 'var(--text-secondary)', letterSpacing: '0.08em', textTransform: 'uppercase',
      }}>
        asset<span style={{ color: 'var(--accent-approve)' }}>.</span>factory
      </span>
    </div>
  );
}

function Kbd({ children }) {
  return <span className="kbd">{children}</span>;
}

function SegProgress({ approved = 0, rejected = 0, total = 1 }) {
  const safeTotal = Math.max(1, total);
  const a = Math.min(100, (approved / safeTotal) * 100);
  const r = Math.min(100 - a, (rejected / safeTotal) * 100);
  return (
    <div style={{
      display: 'flex', height: 6, borderRadius: 999, overflow: 'hidden',
      background: 'var(--bg-elev-3)',
    }}>
      <div style={{ width: `${a}%`, background: 'var(--accent-approve)' }}/>
      <div style={{ width: `${r}%`, background: 'var(--border-strong)' }}/>
    </div>
  );
}

const NAV_ITEMS = [
  { key: 'dashboard', icon: '⌂', label: '/dashboard', to: '/' },
  { key: 'queue',     icon: '≡', label: '/queue',     to: '/queue' },
  { key: 'assets',    icon: '▦', label: '/assets',    to: '/assets' },
  { key: 'batches',   icon: '◫', label: '/batches',   to: '/batches' },
  { key: 'catalog',   icon: '⊞', label: '/catalog',   to: '/catalog' },
  { key: 'export',    icon: '↑', label: '/export',    to: '/export' },
  { key: 'system',    icon: '⚙', label: '/system',    to: '/system' },
  { key: 'settings',  icon: '·',  label: '/settings',  to: '/settings' },
];

function AppSideNav({ active }) {
  // Live SD health pulse for the side-rail footer.
  const [sd, setSd] = useState({ ok: null, host: null });
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const r = await window.api.healthSd();
        if (!cancelled) setSd({ ok: true, host: r?.host || null });
      } catch (err) {
        if (!cancelled) setSd({ ok: false, host: null });
      }
    }
    tick();
    const id = setInterval(tick, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <aside className="app-side">
      <div style={{ padding: '4px 10px 18px' }}><Monogram/></div>
      {NAV_ITEMS.map((item) => (
        <window.Link
          key={item.key}
          to={item.to}
          className={`nav-item ${active === item.key ? 'active' : ''}`}
        >
          <span className="icon">{item.icon}</span>
          <span>{item.label}</span>
        </window.Link>
      ))}
      <div style={{ flex: 1 }}/>
      <div className="footer-status">
        <div>
          SD{' '}
          {sd.ok === null
            ? <span style={{ color: 'var(--text-faint)' }}>● checking</span>
            : sd.ok
              ? <span style={{ color: 'var(--accent-success)' }}>● online</span>
              : <span style={{ color: 'var(--accent-reject)' }}>● offline</span>}
        </div>
        <div>{window.location.host}</div>
      </div>
    </aside>
  );
}

function ApiKeyChip() {
  // Reads localStorage('af_api_key') and listens for af:apikey-changed +
  // cross-tab storage events. Click jumps to /settings for configuration.
  const [hasKey, setHasKey] = useState(() => {
    try { return !!window.localStorage?.getItem('af_api_key'); } catch { return false; }
  });
  useEffect(() => {
    function sync() {
      try { setHasKey(!!window.localStorage.getItem('af_api_key')); } catch { /* ignore */ }
    }
    function onStorage(e) {
      if (e && e.key && e.key !== 'af_api_key') return;
      sync();
    }
    window.addEventListener('storage', onStorage);
    window.addEventListener('af:apikey-changed', sync);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener('af:apikey-changed', sync);
    };
  }, []);
  const cls = `chip chip-small ${hasKey ? 'chip-ok' : 'chip-warn'}`;
  const title = hasKey
    ? 'API 키 설정됨 (Settings에서 변경)'
    : 'API 키 없음 — 쓰기 API 호출은 401. Settings에서 설정하세요.';
  return (
    <window.Link to="/settings" className={cls} title={title} style={{ textDecoration: 'none' }}>
      <span
        aria-hidden
        style={{
          width: 6, height: 6, borderRadius: '50%', display: 'inline-block',
          marginRight: 6,
          background: hasKey ? 'var(--accent-success)' : 'var(--accent-reject)',
        }}
      />
      API {hasKey ? 'set' : 'missing'}
    </window.Link>
  );
}

function AppTopBar() {
  // Live cherry-pick queue snapshot drives the pill numbers.
  const [data, setData] = useState({ batches: null, remaining: null, sdOk: null });
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const [queue, sd] = await Promise.allSettled([
          window.api.cherryPickQueue({ limit: 50 }),
          window.api.healthSd(),
        ]);
        if (cancelled) return;
        const q = queue.status === 'fulfilled' ? queue.value : null;
        setData({
          batches: q?.pending_batches ?? null,
          remaining: q?.total_remaining ?? null,
          sdOk: sd.status === 'fulfilled',
        });
      } catch { /* ignore */ }
    }
    tick();
    const id = setInterval(tick, 10000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <header className="app-topbar">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: data.sdOk === false
            ? 'var(--accent-reject)'
            : data.sdOk === true
              ? 'var(--accent-success)'
              : 'var(--text-faint)',
        }}/>
        <span style={{ color: 'var(--text-secondary)' }}>
          SD {data.sdOk === false ? 'offline' : data.sdOk === true ? 'online' : '…'}
        </span>
      </div>
      <div className="divider"/>
      <span style={{ color: 'var(--text-muted)' }}>
        today queue{' '}
        <b style={{ color: 'var(--accent-approve)' }}>{data.batches ?? '·'}</b> batches
        {data.remaining != null && <> · {data.remaining}장 남음</>}
      </span>
      <div style={{ flex: 1 }}/>
      <ApiKeyChip/>
      <span style={{ color: 'var(--text-faint)' }}>j k nav · ? help</span>
      <div className="avatar">YK</div>
    </header>
  );
}

function AppShell({ active, children }) {
  const Banners = window.PersistentBanners;
  return (
    <div className="app-shell">
      <AppSideNav active={active}/>
      <main className="app-main">
        <AppTopBar/>
        {Banners ? <Banners/> : null}
        <div className="app-screen">{children}</div>
      </main>
    </div>
  );
}

Object.assign(window, { Monogram, Kbd, SegProgress, AppSideNav, AppTopBar, AppShell, ApiKeyChip });

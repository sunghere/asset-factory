/* App entry — mounts the SPA on #root.

   Routing strategy: HTML5 history API behind /app prefix (see router.jsx).
   server.py serves index.html for /app and /app/* so deep links work. */

const { useMemo } = React;

// Legacy screens (/regen, /monitor, /errors) were retired in v0.2:
//   /regen    → superseded by /batches/new (with prefill query params)
//   /monitor  → merged into /system (DB/Worker/Logs blocks)
//   /errors   → replaced by PersistentBanners + ErrorPanel in Dashboard
// We keep a hard redirect for /regen because it's deep-linked from the old
// legacy UI. /monitor and /errors 404 → side nav already points at /system.
function LegacyRedirect({ to }) {
  React.useEffect(() => { window.navigate(to, { replace: true }); }, [to]);
  return null;
}

const ROUTES = [
  { pattern: '/',                       nav: 'dashboard', render: () => <window.Dashboard/> },
  { pattern: '/queue',                  nav: 'queue',     render: () => <window.Queue/> },
  { pattern: '/cherry-pick/:batchId',   nav: 'queue',     render: ({ batchId }) => <window.CherryPick batchId={batchId}/> },
  { pattern: '/assets',                 nav: 'assets',    render: () => <window.Assets/> },
  { pattern: '/assets/:assetId',        nav: 'assets',    render: ({ assetId }) => <window.AssetDetail assetId={assetId}/> },
  { pattern: '/manual',                 nav: 'manual',    render: () => <window.Manual/> },
  { pattern: '/batches',                nav: 'batches',   render: () => <window.Batches/> },
  { pattern: '/batches/new',            nav: 'batches',   render: () => <window.BatchNew/> },
  { pattern: '/batches/:batchId',       nav: 'batches',   render: ({ batchId }) => <window.BatchDetail batchId={batchId}/> },
  { pattern: '/catalog',                nav: 'catalog',   render: () => <window.Catalog/> },
  { pattern: '/export',                 nav: 'export',    render: () => <window.Export/> },
  { pattern: '/system',                 nav: 'system',    render: () => <window.System/> },
  { pattern: '/settings',               nav: 'settings',  render: () => <window.Settings/> },
  { pattern: '/regen',                  nav: 'batches',   render: () => <LegacyRedirect to="/batches/new"/> },
];

function App() {
  // Determine which side-nav item to highlight by re-matching the active route.
  const path = window.useRoute();
  const activeNav = useMemo(() => {
    for (const r of ROUTES) {
      const parts = r.pattern.split('/').filter(Boolean);
      const aParts = path.split('/').filter(Boolean);
      if (parts.length !== aParts.length) continue;
      let ok = true;
      for (let i = 0; i < parts.length; i++) {
        if (parts[i].startsWith(':')) continue;
        if (parts[i] !== aParts[i]) { ok = false; break; }
      }
      if (ok) return r.nav;
    }
    return 'dashboard';
  }, [path]);

  return (
    <window.ErrorBoundary>
      <window.ToastProvider>
        <window.AppShell active={activeNav}>
          <window.Router
            routes={ROUTES}
            fallback={
              <window.EmptyState
                glyph="404"
                title="페이지 없음"
                hint={`경로: ${path}`}
                action={<window.Link to="/" className="btn">대시보드로</window.Link>}
              />
            }
          />
        </window.AppShell>
      </window.ToastProvider>
    </window.ErrorBoundary>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App/>);

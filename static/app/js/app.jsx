/* App entry — mounts the SPA on #root.

   Routing strategy: HTML5 history API behind /app prefix (see router.jsx).
   server.py serves index.html for /app and /app/* so deep links work. */

const { useMemo } = React;

const ROUTES = [
  { pattern: '/',                       nav: 'dashboard', render: () => <window.Dashboard/> },
  { pattern: '/queue',                  nav: 'queue',     render: () => <window.Queue/> },
  { pattern: '/cherry-pick/:batchId',   nav: 'queue',     render: ({ batchId }) => <window.CherryPick batchId={batchId}/> },
  { pattern: '/assets',                 nav: 'assets',    render: () => <window.Assets/> },
  { pattern: '/assets/:assetId',        nav: 'assets',    render: ({ assetId }) => <window.AssetDetail assetId={assetId}/> },
  { pattern: '/batches',                nav: 'batches',   render: () => <window.Batches/> },
  { pattern: '/batches/new',            nav: 'batches',   render: () => <window.BatchNew/> },
  { pattern: '/batches/:batchId',       nav: 'batches',   render: ({ batchId }) => <window.BatchDetail batchId={batchId}/> },
  { pattern: '/catalog',                nav: 'catalog',   render: () => <window.Catalog/> },
  { pattern: '/export',                 nav: 'export',    render: () => <window.Export/> },
  { pattern: '/system',                 nav: 'system',    render: () => <window.System/> },
  { pattern: '/settings',               nav: 'settings',  render: () => <window.Settings/> },
  { pattern: '/regen',                  nav: 'batches',   render: () => <window.Regen/> },
  { pattern: '/monitor',                nav: 'monitor',   render: () => <window.Monitor/> },
  { pattern: '/errors',                 nav: null,        render: () => <window.Errors/> },
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

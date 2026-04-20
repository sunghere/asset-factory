/* Tiny History API router. Routes are matched against location.pathname
   stripped of the /app prefix. Patterns support ":param" segments. */

const { useState, useEffect, useCallback } = React;

const APP_PREFIX = '/app';

function currentPath() {
  let p = window.location.pathname;
  if (p.startsWith(APP_PREFIX)) p = p.slice(APP_PREFIX.length);
  return p || '/';
}

function matchRoute(pattern, path) {
  if (pattern === path) return {};
  const pParts = pattern.split('/').filter(Boolean);
  const aParts = path.split('/').filter(Boolean);
  if (pParts.length !== aParts.length) return null;
  const params = {};
  for (let i = 0; i < pParts.length; i++) {
    const p = pParts[i];
    const a = aParts[i];
    if (p.startsWith(':')) {
      params[p.slice(1)] = decodeURIComponent(a);
    } else if (p !== a) {
      return null;
    }
  }
  return params;
}

function navigate(to) {
  const target = to.startsWith('/') ? APP_PREFIX + (to === '/' ? '/' : to) : to;
  if (target === window.location.pathname) return;
  window.history.pushState({}, '', target);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

function useRoute() {
  const [path, setPath] = useState(currentPath);
  useEffect(() => {
    function onPop() { setPath(currentPath()); }
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  return path;
}

function Link({ to, children, ...rest }) {
  const onClick = useCallback((e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(to);
  }, [to]);
  const href = to.startsWith('/') ? APP_PREFIX + (to === '/' ? '/' : to) : to;
  return <a href={href} onClick={onClick} {...rest}>{children}</a>;
}

// Simple <Router routes={[{ pattern, render }]} /> matcher.
function Router({ routes, fallback }) {
  const path = useRoute();
  for (const r of routes) {
    const params = matchRoute(r.pattern, path);
    if (params) return r.render(params);
  }
  return fallback || null;
}

Object.assign(window, { Router, Link, navigate, useRoute, APP_PREFIX });

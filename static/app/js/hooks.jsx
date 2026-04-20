/* Shared React hooks + small primitives. Exposes globals on window. */

const { useState, useEffect, useRef, useCallback, useMemo, createContext, useContext } = React;

// ─── useAsync ──────────────────────────────────────────────────────
// Run an async fn; expose { data, error, loading, reload }.
function useAsync(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const tickRef = useRef(0);

  const run = useCallback(() => {
    const tick = ++tickRef.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    Promise.resolve()
      .then(fn)
      .then((data) => {
        if (tick === tickRef.current) setState({ data, error: null, loading: false });
      })
      .catch((error) => {
        if (tick === tickRef.current) setState({ data: null, error, loading: false });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { run(); }, [run]);
  return { ...state, reload: run };
}

// ─── useInterval ───────────────────────────────────────────────────
function useInterval(fn, ms) {
  const fnRef = useRef(fn);
  useEffect(() => { fnRef.current = fn; }, [fn]);
  useEffect(() => {
    if (ms == null) return undefined;
    const id = setInterval(() => fnRef.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

// ─── useKeyboard ───────────────────────────────────────────────────
// map: { 'j': handler, 'ArrowRight': handler, 'shift+u': handler }
// Ignored when focus is in an input/textarea/contenteditable.
function useKeyboard(map, deps = []) {
  useEffect(() => {
    function handler(e) {
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.target && e.target.isContentEditable) return;

      const parts = [];
      if (e.ctrlKey || e.metaKey) parts.push('mod');
      if (e.shiftKey) parts.push('shift');
      if (e.altKey) parts.push('alt');
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      parts.push(key);
      const combo = parts.join('+');

      const fn = map[combo] || map[key];
      if (fn) {
        e.preventDefault();
        fn(e);
      }
    }
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

// ─── Toasts (with undo) ────────────────────────────────────────────
const ToastContext = createContext(null);

function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const remove = useCallback((id) => {
    setToasts((ts) => ts.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((toast) => {
    const id = ++idRef.current;
    const ttl = toast.ttl ?? 5000;
    const t = { id, kind: 'info', ...toast };
    setToasts((ts) => [...ts, t]);
    if (ttl > 0) {
      setTimeout(() => {
        setToasts((ts) => ts.filter((x) => x.id !== id));
      }, ttl);
    }
    return id;
  }, []);

  const value = useMemo(() => ({ push, remove, toasts }), [push, remove, toasts]);

  // ESC dismisses the most recent toast (matches "undo never confirm" UX).
  useEffect(() => {
    function onEsc(e) {
      if (e.key !== 'Escape' || toasts.length === 0) return;
      const last = toasts[toasts.length - 1];
      if (last.onUndo) last.onUndo();
      remove(last.id);
    }
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [toasts, remove]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <span className="dot"/>
            <span>{t.message}</span>
            {t.onUndo && (
              <button
                className="action"
                onClick={() => { t.onUndo(); remove(t.id); }}
              >실행취소</button>
            )}
            <span className="esc">esc</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function useToasts() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToasts must be used inside <ToastProvider>');
  return ctx;
}

// ─── useSSE ────────────────────────────────────────────────────────
// Subscribes to /api/events. Buffers events and flushes via rAF to avoid
// re-render storms when the server publishes bursts.
function useSSE(onBatch, { active = true } = {}) {
  const queueRef = useRef([]);
  const rafRef = useRef(0);
  const onBatchRef = useRef(onBatch);

  useEffect(() => { onBatchRef.current = onBatch; }, [onBatch]);

  useEffect(() => {
    if (!active) return undefined;
    const es = new EventSource('/api/events');

    function flush() {
      rafRef.current = 0;
      const items = queueRef.current;
      queueRef.current = [];
      if (items.length && onBatchRef.current) onBatchRef.current(items);
    }

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        queueRef.current.push({ ...data, _at: Date.now() });
        if (!rafRef.current) rafRef.current = requestAnimationFrame(flush);
      } catch { /* ignore */ }
    };
    es.onerror = () => { /* EventSource auto-reconnects */ };

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      es.close();
    };
  }, [active]);
}

// ─── ErrorBoundary ─────────────────────────────────────────────────
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error('[ErrorBoundary]', error, info); }
  reset = () => this.setState({ error: null });
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32 }}>
          <div className="error-banner" style={{ marginBottom: 16 }}>
            <span>⚠</span>
            <span>화면이 크래시했습니다 · {String(this.state.error.message || this.state.error)}</span>
          </div>
          <button className="btn" onClick={this.reset}>다시 시도</button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ─── EmptyState ────────────────────────────────────────────────────
function EmptyState({ glyph = '∅', title, hint, action }) {
  return (
    <div className="empty-state">
      <div className="glyph">{glyph}</div>
      {title && <div className="title">{title}</div>}
      {hint && <div className="hint">{hint}</div>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}

// ─── Skeleton ──────────────────────────────────────────────────────
function Skeleton({ width = '100%', height = 14, style }) {
  return <div className="skeleton" style={{ width, height, ...style }}/>;
}

// ─── Thumb ─────────────────────────────────────────────────────────
// Renders a candidate / asset thumbnail. Falls back to a placeholder block.
// Note: backend currently has no resized variant; loads original image.
// See docs/asset-factory-redesign-followups.md for the thumbnail-endpoint gap.
function Thumb({ src, alt, state, badge, warn, caption, onClick, style }) {
  const [errored, setErrored] = useState(false);
  return (
    <div
      className={`thumb ${state || ''}`}
      onClick={onClick}
      style={style}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      {badge && <span className={`bdg ${badge.kind || ''}`}>{badge.label}</span>}
      {warn && (
        <span
          className={`bdg warn ${warn.kind || 'fail'}`}
          title={warn.title || undefined}
          aria-label={warn.title || warn.label}
        >{warn.label || '!'}</span>
      )}
      {src && !errored ? (
        <img src={src} alt={alt || ''} loading="lazy" onError={() => setErrored(true)}/>
      ) : (
        <div className="placeholder">{errored ? '×' : '…'}</div>
      )}
      {caption && (
        <div className="cap">
          <span>{caption.left}</span>
          <span>{caption.right}</span>
        </div>
      )}
    </div>
  );
}

// ─── PageInfo ──────────────────────────────────────────────────────
// 설계 문서 스타일의 페이지 디스크립션을 화면에 크게 노출하는 대신
// 작은 ⓘ 버튼에 담아두는 팝오버. 외부 클릭 시 닫힘.
function PageInfo({ title, text }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (!ref.current || !ref.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <span className="page-info" ref={ref}>
      <button
        type="button"
        className="page-info-btn"
        aria-label="이 페이지 설명"
        title="이 페이지 설명"
        onClick={() => setOpen((o) => !o)}
      >i</button>
      {open && (
        <div className="page-info-pop" role="tooltip">
          {title && <div className="t">{title}</div>}
          <div>{text}</div>
        </div>
      )}
    </span>
  );
}

// ─── PageToolbar ──────────────────────────────────────────────────
// 일반 페이지 상단의 얇은 툴바. 큰 타이틀/디스크립션을 대체.
// left: 카운트 chip 등, right: 액션 버튼 + PageInfo.
function PageToolbar({ left, right, info }) {
  return (
    <div className="page-toolbar">
      <div className="page-toolbar-left">{left}</div>
      <div className="page-toolbar-right">
        {right}
        {info && <PageInfo {...info}/>}
      </div>
    </div>
  );
}

Object.assign(window, {
  useAsync,
  useInterval,
  useKeyboard,
  useSSE,
  ToastProvider,
  useToasts,
  ErrorBoundary,
  EmptyState,
  Skeleton,
  Thumb,
  PageInfo,
  PageToolbar,
});

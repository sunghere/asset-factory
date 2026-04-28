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

// ─── ErrorPanel ────────────────────────────────────────────────────
// 화면 안에서 쓰는 공용 에러 패널. ApiError (status 5xx) 인 경우 큰 Retry
// 버튼을 노출하고, body 가 있으면 <details> 에 접어서 보여준다.
//
// props:
//   - error : Error | ApiError | null
//   - onRetry : () => void   // (optional) 있으면 항상 Retry 노출
//   - compact : boolean      // marginBottom 축소 (리스트 내부용)
function ErrorPanel({ error, onRetry, compact }) {
  if (!error) return null;
  const status = error.status || null;
  const statusText = error.statusText || '';
  const is5xx = status != null && status >= 500 && status < 600;
  const isAuth = status === 401 || status === 403;
  const message = String(error.message || error);
  const body = error.body;
  const bodyText = body
    ? (typeof body === 'string' ? body : JSON.stringify(body, null, 2))
    : null;

  return (
    <div
      className="error-banner"
      role="alert"
      style={{
        flexDirection: 'column',
        alignItems: 'stretch',
        gap: 8,
        marginBottom: compact ? 8 : 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>⚠</span>
        <span style={{ flex: 1, wordBreak: 'break-word' }}>
          {status ? <b style={{ marginRight: 6 }}>{status} {statusText}</b> : null}
          {message}
        </span>
        {(onRetry || is5xx) && (
          <button
            className="btn btn-primary"
            onClick={onRetry}
            style={{ whiteSpace: 'nowrap' }}
            disabled={!onRetry}
            title={onRetry ? '요청을 다시 보냅니다' : '재시도 핸들러 없음'}
          >
            ↻ 재시도
          </button>
        )}
        {isAuth && (
          <button
            className="btn"
            onClick={() => { window.navigate && window.navigate('/settings'); }}
            style={{ whiteSpace: 'nowrap' }}
          >
            API Key 설정
          </button>
        )}
      </div>
      {bodyText && (
        <details>
          <summary style={{ cursor: 'pointer', fontSize: 11, color: 'var(--text-muted)' }}>
            응답 본문 펼치기
          </summary>
          <pre
            style={{
              margin: '6px 0 0',
              padding: 8,
              background: 'rgba(0,0,0,0.25)',
              borderRadius: 4,
              fontSize: 11,
              maxHeight: 240,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >{bodyText}</pre>
        </details>
      )}
    </div>
  );
}

// ─── PersistentBanners ─────────────────────────────────────────────
// AppShell 상단에 전역 알림을 노출한다:
//  - ComfyUI offline : /api/comfyui/health 의 ok=false (PLAN Task 7)
//  - API key 없음 : localStorage('af_api_key') 비어있음
// 각 배너는 '숨김' 상태를 sessionStorage 에 저장해 세션 동안만 닫힌 상태를 유지.
// A1111 가 죽어도 배너 띄우지 않는다 — deprecated, ComfyUI 만 평가.
function PersistentBanners() {
  const [sdOk, setSdOk] = useState(null);
  const [apiKeyMissing, setApiKeyMissing] = useState(() => {
    try { return !window.localStorage?.getItem('af_api_key'); } catch { return false; }
  });
  const [dismissedSd, setDismissedSd] = useState(() => {
    try { return sessionStorage.getItem('banner_sd_dismissed') === '1'; } catch { return false; }
  });
  const [dismissedKey, setDismissedKey] = useState(() => {
    try { return sessionStorage.getItem('banner_apikey_dismissed') === '1'; } catch { return false; }
  });

  // Poll ComfyUI health — same cadence (15s). 응답 본문의 ok 필드만 평가.
  // /api/comfyui/health 는 항상 200 + ok 분기 (PLAN §3.1.1).
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const data = await window.api.comfyuiHealth();
        if (!cancelled) setSdOk(data?.ok === true);
      } catch { if (!cancelled) setSdOk(false); }
    }
    tick();
    const id = setInterval(tick, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Watch af_api_key — fires when Settings saves through another tab / manual change.
  useEffect(() => {
    function onStorage(e) {
      if (e.key && e.key !== 'af_api_key') return;
      try { setApiKeyMissing(!window.localStorage.getItem('af_api_key')); } catch { /* ignore */ }
    }
    window.addEventListener('storage', onStorage);
    window.addEventListener('af:apikey-changed', onStorage);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener('af:apikey-changed', onStorage);
    };
  }, []);

  const showSd = sdOk === false && !dismissedSd;
  const showKey = apiKeyMissing && !dismissedKey;
  if (!showSd && !showKey) return null;

  const dismissSd = () => {
    try { sessionStorage.setItem('banner_sd_dismissed', '1'); } catch { /* ignore */ }
    setDismissedSd(true);
  };
  const dismissKey = () => {
    try { sessionStorage.setItem('banner_apikey_dismissed', '1'); } catch { /* ignore */ }
    setDismissedKey(true);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '8px 24px 0' }}>
      {showSd && (
        <div
          className="error-banner"
          role="alert"
          style={{
            background: 'rgba(255, 184, 107, 0.08)',
            borderColor: 'rgba(255, 184, 107, 0.35)',
            color: 'var(--accent-warning)',
          }}
        >
          <span>●</span>
          <span style={{ flex: 1 }}>
            <b>ComfyUI 오프라인</b> — /api/comfyui/health 가 ok=false. 새 배치는 큐에만 쌓이고 생성이 멈춥니다.
          </span>
          <a
            href="/system"
            onClick={(e) => { e.preventDefault(); window.navigate && window.navigate('/system'); }}
            className="btn"
          >
            /system 열기
          </a>
          <button className="btn" onClick={dismissSd} title="이 세션 동안 숨김">✕</button>
        </div>
      )}
      {showKey && (
        <div
          className="error-banner"
          role="status"
          style={{
            background: 'rgba(143, 184, 255, 0.08)',
            borderColor: 'rgba(143, 184, 255, 0.35)',
            color: 'var(--accent-pick)',
          }}
        >
          <span>🔑</span>
          <span style={{ flex: 1 }}>
            <b>API Key 미설정</b> — 쓰기 요청이 401 로 거부될 수 있습니다. /settings 에서 키를 등록하세요.
          </span>
          <a
            href="/settings"
            onClick={(e) => { e.preventDefault(); window.navigate && window.navigate('/settings'); }}
            className="btn"
          >
            /settings 이동
          </a>
          <button className="btn" onClick={dismissKey} title="이 세션 동안 숨김">✕</button>
        </div>
      )}
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

// ─── Dialog ────────────────────────────────────────────────────────
// 접근성 기본(focus trap, Escape 닫기, backdrop 클릭 닫기, role=dialog,
// aria-modal, labelledby/describedby)을 한 번만 구현해두고 화면들이 가져다
// 쓴다. 내부적으로 React portal 없이 DOM 에 고정 위치로 렌더링한다.
//
// props:
//   open        : boolean
//   onClose()   : 닫기 요청 (ESC / backdrop / ✕ 버튼)
//   title       : string | ReactNode (h2 로 렌더)
//   description : string | ReactNode (선택, subtitle)
//   footer      : ReactNode (선택, 우하단 액션 row)
//   size        : 'sm' | 'md' | 'lg' (기본 md = 520px)
//   children    : 본문
function Dialog({ open, onClose, title, description, footer, size = 'md', children }) {
  const backdropRef = useRef(null);
  const panelRef = useRef(null);
  const lastActiveRef = useRef(null);
  const titleId = useMemo(
    () => `dlg-title-${Math.random().toString(36).slice(2, 8)}`,
    []
  );
  const descId = useMemo(
    () => `dlg-desc-${Math.random().toString(36).slice(2, 8)}`,
    []
  );

  useEffect(() => {
    if (!open) return undefined;
    lastActiveRef.current = document.activeElement;

    // 첫 포커싱 가능한 요소에 포커스 이동.
    const t = setTimeout(() => {
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = panel.querySelector(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      (focusable || panel).focus();
    }, 0);

    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose && onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const panel = panelRef.current;
      if (!panel) return;
      const nodes = panel.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKey);
    // body 스크롤 잠금(간단).
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      clearTimeout(t);
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      const prev = lastActiveRef.current;
      if (prev && prev.focus) prev.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  const widths = { sm: 360, md: 520, lg: 880 };
  const width = widths[size] || widths.md;

  return (
    <div
      ref={backdropRef}
      className="dlg-backdrop"
      onMouseDown={(e) => {
        if (e.target === backdropRef.current) onClose && onClose();
      }}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(10,12,16,0.62)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className="dlg-panel panel-card"
        style={{
          width,
          maxWidth: 'calc(100vw - 32px)',
          maxHeight: 'calc(100vh - 48px)',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          padding: 20,
        }}
      >
        {(title || onClose) && (
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <div style={{ flex: 1 }}>
              {title && <h2 id={titleId} style={{ margin: 0, fontSize: 18 }}>{title}</h2>}
              {description && (
                <div
                  id={descId}
                  style={{ marginTop: 4, color: 'var(--text-faint)', fontSize: 13 }}
                >{description}</div>
              )}
            </div>
            {onClose && (
              <button
                type="button"
                className="btn ghost"
                onClick={onClose}
                aria-label="닫기"
                style={{ padding: '4px 10px' }}
              >✕</button>
            )}
          </div>
        )}
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>{children}</div>
        {footer && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            {footer}
          </div>
        )}
      </div>
    </div>
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

// ─── useAnalytics ──────────────────────────────────────────────────
// Opt-in analytics stub. No network, no storage side-effects — we just
// emit console.debug lines when localStorage('af_analytics') === 'on'
// so QA can peek at engagement signals (cherrypick session stats,
// bulk action usage, …) without wiring a third-party SDK yet.
//
// Usage:
//   const track = window.useAnalytics('cherrypick');
//   track('session.open', { batchId, total: 40 });
//   track('pick', { verdict: 'approve', slot: 0 });
//
// The returned function is stable for the component's lifetime.
function useAnalytics(namespace = 'app') {
  return useCallback((event, payload = {}) => {
    let enabled = false;
    try {
      enabled = window.localStorage?.getItem('af_analytics') === 'on';
    } catch { /* storage disabled — treat as off */ }
    if (!enabled) return;
    const stamp = new Date().toISOString().slice(11, 23);
    // Keep the log line short; a single object on the end makes devtools
    // click-to-expand cheap. No deep clone — payload is expected to be
    // serialisable scalars.
    // eslint-disable-next-line no-console
    console.debug(`[af·${namespace}] ${stamp} ${event}`, payload);
  }, [namespace]);
}

Object.assign(window, {
  useAsync,
  useInterval,
  useKeyboard,
  useSSE,
  useAnalytics,
  ToastProvider,
  useToasts,
  ErrorBoundary,
  EmptyState,
  ErrorPanel,
  PersistentBanners,
  Skeleton,
  Thumb,
  Dialog,
  PageInfo,
  PageToolbar,
});

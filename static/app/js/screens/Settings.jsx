/* Settings — local-only preferences persisted to window.localStorage.
   Keys:
     af_api_key           — X-API-Key used by api.jsx (also used by the legacy UI)
     af_grid_cols         — preferred grid column count for cherry-pick / gallery
     af_auto_advance      — 'on' | 'off'  — whether Enter/x advances to the next candidate
     af_keymap            — 'on' | 'off'  — global keyboard shortcuts in CherryPick
     af_motion            — 'full' | 'reduced' | 'none' — user-level motion preference
     af_analytics         — 'on' | 'off'  — opt-in console.debug analytics (local-only)
   None of these are sent to the backend; we only use them to style the SPA. */

const { useState, useEffect } = React;

const LS_KEYS = {
  apiKey: 'af_api_key',
  gridCols: 'af_grid_cols',
  autoAdvance: 'af_auto_advance',
  keymap: 'af_keymap',
  motion: 'af_motion',
  analytics: 'af_analytics',
};

function mask(s) {
  if (!s) return '';
  if (s.length <= 6) return '*'.repeat(s.length);
  return s.slice(0, 3) + '…'.repeat(3) + s.slice(-3);
}

// legacy 저장값('true'/'false')이 남아있을 수 있어 관대하게 정규화한다.
function normalizeOnOff(raw, def) {
  if (raw === 'on' || raw === 'true') return 'on';
  if (raw === 'off' || raw === 'false') return 'off';
  return def;
}

function Settings() {
  const toasts = window.useToasts();
  const [apiKey, setApiKey] = useState(() => window.localStorage.getItem(LS_KEYS.apiKey) || '');
  const [apiKeyDraft, setApiKeyDraft] = useState(apiKey);
  const [gridCols, setGridCols] = useState(() => window.localStorage.getItem(LS_KEYS.gridCols) || '5');
  const [autoAdvance, setAutoAdvance] = useState(() => normalizeOnOff(window.localStorage.getItem(LS_KEYS.autoAdvance), 'on'));
  const [keymap, setKeymap] = useState(() => normalizeOnOff(window.localStorage.getItem(LS_KEYS.keymap), 'on'));
  const [motion, setMotion] = useState(() => window.localStorage.getItem(LS_KEYS.motion) || 'full');
  const [analytics, setAnalytics] = useState(() => normalizeOnOff(window.localStorage.getItem(LS_KEYS.analytics), 'off'));

  // Debounced autosave for the simple toggles (API key saved explicitly).
  useEffect(() => { window.localStorage.setItem(LS_KEYS.gridCols, gridCols); }, [gridCols]);
  useEffect(() => { window.localStorage.setItem(LS_KEYS.autoAdvance, autoAdvance); }, [autoAdvance]);
  useEffect(() => { window.localStorage.setItem(LS_KEYS.keymap, keymap); }, [keymap]);
  useEffect(() => { window.localStorage.setItem(LS_KEYS.motion, motion); }, [motion]);
  useEffect(() => { window.localStorage.setItem(LS_KEYS.analytics, analytics); }, [analytics]);

  function saveApiKey() {
    const v = apiKeyDraft.trim();
    if (v) window.localStorage.setItem(LS_KEYS.apiKey, v);
    else window.localStorage.removeItem(LS_KEYS.apiKey);
    setApiKey(v);
    // Notify same-tab listeners (PersistentBanners, TopBar chip). Cross-tab
    // changes are picked up by the native 'storage' event.
    try { window.dispatchEvent(new CustomEvent('af:apikey-changed')); } catch { /* ignore */ }
    toasts.push({ kind: 'success', message: v ? 'API key 저장됨' : 'API key 삭제됨' });
  }

  const versionInfo = window.__AF_VERSION__ || { version: 'dev', host: window.location.host };

  return (
    <div>
      <window.PageToolbar
        info={{
          title: 'settings',
          text: '로컬 전용 환경설정. X-API-Key, 그리드 열 수, Enter 후 자동진행, 모션 선호도를 브라우저 localStorage 에 저장합니다. 서버 전송 없음.',
        }}
      />

      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1fr' }}>
        <div className="panel-card">
          <h3>API key</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 0 }}>
            <code>X-API-Key</code> 헤더로 모든 POST/PATCH/DELETE 요청에 자동 첨부됩니다. 현재 저장값:&nbsp;
            <code>{mask(apiKey) || '— 없음 —'}</code>
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="input"
              type="password"
              value={apiKeyDraft}
              onChange={(e) => setApiKeyDraft(e.target.value)}
              placeholder="key를 붙여넣으세요"
              style={{ flex: 1 }}
            />
            <button type="button" className="btn" onClick={saveApiKey}>저장</button>
            <button
              type="button"
              className="btn"
              onClick={() => { setApiKeyDraft(''); }}
            >지우기</button>
          </div>
          <p style={{ color: 'var(--text-faint)', fontSize: 11, marginTop: 10 }}>
            (서버에 전송되지 않고 브라우저 저장소에만 남습니다. 공용 PC면 쓰지 마세요.)
          </p>
        </div>

        <div className="panel-card">
          <h3>표시 옵션</h3>
          <div className="form-grid">
            <label>
              <span>cherry-pick 그리드 열</span>
              <select className="input" value={gridCols} onChange={(e) => setGridCols(e.target.value)}>
                <option value="4">4열</option>
                <option value="5">5열</option>
                <option value="6">6열</option>
                <option value="7">7열</option>
              </select>
            </label>
            <label>
              <span>자동 진행 (Enter / x 후)</span>
              <select className="input" value={autoAdvance} onChange={(e) => setAutoAdvance(e.target.value)}>
                <option value="on">on</option>
                <option value="off">off</option>
              </select>
            </label>
            <label>
              <span>CherryPick 키맵</span>
              <select className="input" value={keymap} onChange={(e) => setKeymap(e.target.value)}>
                <option value="on">on (j/k/Enter/x/…)</option>
                <option value="off">off (? 만 유지)</option>
              </select>
            </label>
            <label style={{ gridColumn: 'span 2' }}>
              <span>모션</span>
              <select className="input" value={motion} onChange={(e) => setMotion(e.target.value)}>
                <option value="full">full (기본)</option>
                <option value="reduced">reduced (부드러움 최소화)</option>
                <option value="none">none (모든 애니메이션 끄기)</option>
              </select>
            </label>
            <label style={{ gridColumn: 'span 2' }}>
              <span>analytics (opt-in · console.debug)</span>
              <select className="input" value={analytics} onChange={(e) => setAnalytics(e.target.value)}>
                <option value="off">off (기본 · 아무 것도 기록하지 않음)</option>
                <option value="on">on (개발자 도구 콘솔에 af· 로그 출력)</option>
              </select>
            </label>
          </div>
          <p style={{ color: 'var(--text-faint)', fontSize: 11, marginTop: 10 }}>
            자동 저장. CSS 변수는 다음 새로고침에 반영됩니다. analytics 는
            서버 전송 없이 <code>console.debug</code> 로만 남으므로 QA 용입니다.
          </p>
        </div>

        <div className="panel-card" style={{ gridColumn: 'span 2' }}>
          <h3>정보</h3>
          <dl className="meta-block">
            <dt>version</dt><dd>{versionInfo.version}</dd>
            <dt>host</dt><dd>{versionInfo.host}</dd>
            <dt>API prefix</dt><dd>/api</dd>
            <dt>SPA 경로</dt><dd>{window.APP_PREFIX || '/app'}</dd>
            <dt>localStorage keys</dt><dd>{Object.values(LS_KEYS).join(', ')}</dd>
          </dl>
        </div>
      </div>
    </div>
  );
}

window.Settings = Settings;

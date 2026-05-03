/* BatchNew — 3-step wizard for POST /api/batches (ComfyUI workflow 곱집합).
   Step 1: identity (project + asset_key + category)
   Step 2: spec    (workflow_category + variants + params overrides + prompts + seeds)
   Step 3: review  (JSON preview + 200+ 경고 + confirm → fire request)
   Backed by DesignBatchRequest. 곱집합:
     prompts × workflow_variants × workflow_params_overrides × seeds. */

const { useState, useMemo, useEffect } = React;

// URL ?workflow_category=X&workflow_variant=Y 파싱 — Catalog 의 workflow 카드에서
// "이 변형으로 batch 만들기" 프리필 진입을 위한 쿼리 hook.
function _readPrefill() {
  try {
    const sp = new URLSearchParams(window.location.search || '');
    return {
      workflow_category: sp.get('workflow_category') || null,
      workflow_variant: sp.get('workflow_variant') || null,
      project: sp.get('project') || null,
      asset_key: sp.get('asset_key') || null,
      category: sp.get('category') || null,
    };
  } catch (_) { return {}; }
}

const PRESETS = [
  { id: 'fast',     label: 'fast',     seeds_per_combo: 4 },
  { id: 'standard', label: 'standard', seeds_per_combo: 16 },
  { id: 'final',    label: 'final',    seeds_per_combo: 64 },
];

const PROMPT_MODES = [
  { id: 'auto',    label: 'auto',    desc: 'subject 명시/template 유무로 자동' },
  { id: 'legacy',  label: 'legacy',  desc: 'prompt 통째 그대로' },
  { id: 'subject', label: 'subject', desc: 'variant 의 prompt_template 합성' },
];

function _safeParseJson(text, fallback) {
  if (!text || !text.trim()) return fallback;
  try { return JSON.parse(text); } catch { return null; }
}

function BatchNew() {
  const toasts = window.useToasts();
  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);
  const catalog = window.useAsync(() => window.api.comfyuiCatalog().catch(() => null), []);

  const [step, setStep] = useState(1);

  const [project, setProject] = useState('default-project');
  const [assetKey, setAssetKey] = useState('');
  const [category, setCategory] = useState('character');

  const [workflowCategory, setWorkflowCategory] = useState('');
  const [selectedVariants, setSelectedVariants] = useState([]);
  // overrides: array of JSON-text strings (parsed at submit). 빈 "{}" 는
  // variant default 그대로 (가장 일반적인 케이스). 한 줄에 한 override.
  const [overridesText, setOverridesText] = useState(['{}']);

  const [prompts, setPrompts] = useState(['']);
  const [promptMode, setPromptMode] = useState('auto');
  const [subject, setSubject] = useState('');
  const [styleExtra, setStyleExtra] = useState('');
  const [negative, setNegative] = useState('');

  const [seedMode, setSeedMode] = useState('random');
  const [seedsText, setSeedsText] = useState('');
  const [seedsPerCombo, setSeedsPerCombo] = useState(16);
  const [presetId, setPresetId] = useState('standard');

  // common — variant default 의 *override*. None 이면 variant default 그대로.
  const [stepsOverride, setStepsOverride] = useState('');
  const [cfgOverride, setCfgOverride] = useState('');
  const [samplerOverride, setSamplerOverride] = useState('');
  const [maxColors, setMaxColors] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [showJsonPreview, setShowJsonPreview] = useState(true);

  // workflows 카탈로그 → category 별로 묶은 변형 목록.
  // shape: { sprite: [{id, label, ...}, ...], illustration: [...] }
  const variantsByCategory = useMemo(() => {
    const wfs = catalog.data?.workflows || [];
    const grouped = {};
    for (const w of wfs) {
      const cat = w.category || (w.id || '').split(':')[0] || 'unknown';
      const variantName = (w.id || '').split(':')[1] || w.label || '';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push({ ...w, variantName });
    }
    return grouped;
  }, [catalog.data]);

  const workflowCategories = useMemo(
    () => Object.keys(variantsByCategory).sort(),
    [variantsByCategory],
  );

  // 카탈로그 도착 후 첫 화면 default — workflowCategory 가 비어 있으면 첫
  // 카테고리 자동 선택. prefill 우선.
  useEffect(() => {
    const pf = _readPrefill();
    if (pf.project) setProject(pf.project);
    if (pf.category) setCategory(pf.category);
    if (pf.asset_key) setAssetKey(pf.asset_key);

    if (!workflowCategory && workflowCategories.length) {
      const target = pf.workflow_category && workflowCategories.includes(pf.workflow_category)
        ? pf.workflow_category
        : workflowCategories[0];
      setWorkflowCategory(target);
      const vs = variantsByCategory[target] || [];
      const primary = vs.find((v) => v.variantName === pf.workflow_variant)
        || vs.find((v) => /pixel_alpha|primary/i.test(v.variantName))
        || vs[0];
      if (primary) setSelectedVariants([primary.variantName]);
    }
    if (pf.workflow_category || pf.workflow_variant) setStep(2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowCategories.join('|')]);

  const variantsHere = variantsByCategory[workflowCategory] || [];

  const seedList = useMemo(() => {
    if (seedMode !== 'fixed') return null;
    return seedsText.split(/[,\s]+/).map((s) => parseInt(s, 10)).filter((n) => Number.isInteger(n));
  }, [seedMode, seedsText]);

  // overrides 파싱 — 잘못된 JSON 은 null 로 표시 (UI 에서 빨간 hint).
  const overridesParsed = useMemo(
    () => overridesText.map((t) => _safeParseJson(t, {})),
    [overridesText],
  );
  const overridesValid = overridesParsed.every((o) => o !== null);

  const expandedCount = useMemo(() => {
    const p = prompts.filter((s) => s.trim()).length;
    const v = selectedVariants.length;
    const o = Math.max(1, overridesParsed.filter((x) => x !== null).length);
    const s = seedMode === 'fixed' ? Math.max(1, (seedList || []).length) : Math.max(1, seedsPerCombo);
    return p * v * o * s;
  }, [prompts, selectedVariants, overridesParsed, seedMode, seedList, seedsPerCombo]);

  function applyPreset(id) {
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    setPresetId(id);
    setSeedsPerCombo(p.seeds_per_combo);
  }

  const step1Valid = project.trim() && assetKey.trim() && category.trim();
  const step2Valid =
    prompts.some((p) => p.trim()) &&
    workflowCategory &&
    selectedVariants.length > 0 &&
    overridesValid &&
    expandedCount > 0;
  const hasInvalidFixedSeed =
    seedMode === 'fixed' && seedsText.trim() && (!seedList || seedList.length === 0);

  const specPayload = useMemo(() => ({
    asset_key: assetKey.trim(),
    project: project.trim(),
    category: category.trim(),
    workflow_category: workflowCategory,
    workflow_variants: selectedVariants,
    workflow_params_overrides: overridesParsed.map((o) => o || {}),
    prompts: prompts.map((p) => p.trim()).filter(Boolean),
    prompt_mode: promptMode,
    subject: subject.trim() || null,
    style_extra: styleExtra.trim() || null,
    seeds: seedMode === 'fixed' ? seedList : null,
    seeds_per_combo: seedsPerCombo,
    common: {
      steps: stepsOverride === '' ? null : Math.max(1, Math.min(200, Number(stepsOverride))),
      cfg: cfgOverride === '' ? null : Math.max(0, Math.min(30, Number(cfgOverride))),
      sampler: samplerOverride.trim() || null,
      negative_prompt: negative.trim() || null,
      max_colors: maxColors === '' ? null : Math.max(1, Math.min(256, Number(maxColors))),
      max_retries: 3,
    },
  }), [
    assetKey, project, category, workflowCategory, selectedVariants,
    overridesParsed, prompts, promptMode, subject, styleExtra,
    seedMode, seedList, seedsPerCombo, stepsOverride, cfgOverride,
    samplerOverride, negative, maxColors,
  ]);

  async function submit() {
    setSubmitting(true);
    try {
      const resp = await window.api.createDesignBatch(specPayload);
      setResult(resp);
      toasts.push({
        kind: 'success',
        message: `batch 생성됨 · ${resp.batch_id} (${resp.expanded_count}장, ETA ${resp.estimated_eta_seconds}s)`,
        ttl: 8000,
      });
    } catch (e) {
      toasts.push({ kind: 'error', message: '생성 실패: ' + (e.message || e), ttl: 8000 });
    } finally {
      setSubmitting(false);
    }
  }

  const overLimit = expandedCount >= 200;

  return (
    <div>
      <window.PageToolbar
        left={
          <a href="/app/batches" onClick={(e) => { e.preventDefault(); window.navigate('/batches'); }}
             style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>← batches</a>
        }
        right={
          <span className="chip" style={overLimit ? { color: 'var(--accent-warning)' } : undefined}>
            예상 <b>{expandedCount}</b>장{overLimit ? ' ⚠' : ''}
          </span>
        }
        info={{
          title: 'batch · new',
          text: '3-step 마법사. ComfyUI workflow 곱집합 — prompts × workflow_variants × workflow_params_overrides × seeds. 200장 초과 시 경고. review 에서 최종 JSON 미리보기 후 POST.',
        }}
      />

      <div className="wizard-steps">
        <div className={`step ${step === 1 ? 'active' : ''} ${step > 1 ? 'done' : ''}`}>1 · target</div>
        <div className={`step ${step === 2 ? 'active' : ''} ${step > 2 ? 'done' : ''}`}>2 · workflow</div>
        <div className={`step ${step === 3 ? 'active' : ''}`}>3 · review</div>
      </div>

      {result && (
        <div className="panel-card" style={{ borderColor: 'var(--accent-approve)', marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <h3 style={{ margin: 0, color: 'var(--accent-approve)' }}>✓ 생성 완료</h3>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                {result.batch_id} · expanded {result.expanded_count} · ETA ~{result.estimated_eta_seconds}s
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn" onClick={() => { setResult(null); setStep(1); }}>다시 만들기</button>
              <a className="btn btn-primary"
                 href={`/app/batches/${result.batch_id}`}
                 onClick={(e) => { e.preventDefault(); window.navigate(`/batches/${result.batch_id}`); }}
              >batch 상세</a>
              <a className="btn"
                 href={`/app/cherry-pick/${result.batch_id}`}
                 onClick={(e) => { e.preventDefault(); window.navigate(`/cherry-pick/${result.batch_id}`); }}
              >cherry-pick</a>
            </div>
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="panel-card">
          <h3>1. Target</h3>
          <div className="form-grid">
            <label>
              <span>project</span>
              {projects.data?.items?.length ? (
                <select className="input" value={project} onChange={(e) => setProject(e.target.value)}>
                  {projects.data.items.map((p) => {
                    const id = typeof p === 'string' ? p : (p.id ?? p.name ?? String(p));
                    const label = typeof p === 'string' ? p : (p.name ?? p.id ?? String(p));
                    return <option key={id} value={id}>{label}</option>;
                  })}
                </select>
              ) : (
                <input className="input" value={project} onChange={(e) => setProject(e.target.value)}/>
              )}
            </label>
            <label>
              <span>category (asset)</span>
              <input className="input" value={category} onChange={(e) => setCategory(e.target.value)}/>
            </label>
            <label style={{ gridColumn: 'span 2' }}>
              <span>asset_key</span>
              <input className="input" value={assetKey} onChange={(e) => setAssetKey(e.target.value)} placeholder="e.g. marine_v2_idle"/>
              {!assetKey.trim() && <div className="hint" style={{ color: 'var(--accent-reject)' }}>asset_key 는 필수입니다.</div>}
            </label>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
            <button className="btn btn-primary" disabled={!step1Valid} onClick={() => setStep(2)}>다음 →</button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div style={{ display: 'grid', gap: 14, gridTemplateColumns: '2fr 1fr' }}>
          {/* LEFT — workflow + prompts */}
          <div className="panel-card">
            <h3>2. Workflow</h3>
            {workflowCategories.length === 0 ? (
              <div className="hint">
                ComfyUI 카탈로그를 불러올 수 없습니다. /system 에서 ComfyUI 연결 상태를 확인하세요.
              </div>
            ) : (
              <>
                <label className="block">
                  <span>workflow_category</span>
                  <select
                    className="input"
                    value={workflowCategory}
                    onChange={(e) => {
                      setWorkflowCategory(e.target.value);
                      // 카테고리 바꾸면 variant 선택 초기화 — 카테고리 간 cross 금지.
                      setSelectedVariants([]);
                    }}
                  >
                    {workflowCategories.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>

                <label className="block" style={{ marginTop: 10 }}>
                  <span>workflow_variants (matrix axis)</span>
                </label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {variantsHere.map((v) => {
                    const sel = selectedVariants.includes(v.variantName);
                    return (
                      <span
                        key={v.id}
                        className={`pill ${sel ? 'active' : ''}`}
                        onClick={() => setSelectedVariants((xs) =>
                          sel ? xs.filter((x) => x !== v.variantName) : [...xs, v.variantName])}
                        title={v.label}
                      >{v.variantName}</span>
                    );
                  })}
                </div>
                {selectedVariants.length === 0 && (
                  <div className="hint" style={{ color: 'var(--accent-reject)', marginTop: 6 }}>
                    최소 1개 variant 가 필요합니다.
                  </div>
                )}
              </>
            )}

            <h3 style={{ marginTop: 18 }}>2b. workflow_params_overrides</h3>
            <div className="hint" style={{ marginBottom: 6 }}>
              각 행은 <code>patch_workflow</code> 인자 dict. 빈 <code>{'{}'}</code> 는 variant default 를 그대로 사용. 예: <code>{'{"controlnet_strength": 0.7}'}</code>
            </div>
            {overridesText.map((t, i) => {
              const parsed = overridesParsed[i];
              const bad = parsed === null;
              return (
                <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                  <input
                    className="input"
                    value={t}
                    style={{ flex: 1, color: bad ? 'var(--accent-reject)' : undefined, fontFamily: 'var(--font-mono)', fontSize: 12 }}
                    onChange={(e) => setOverridesText((xs) => xs.map((x, j) => j === i ? e.target.value : x))}
                  />
                  {overridesText.length > 1 && (
                    <button type="button" className="btn"
                            onClick={() => setOverridesText((xs) => xs.filter((_, j) => j !== i))}>✕</button>
                  )}
                </div>
              );
            })}
            <button type="button" className="btn"
                    onClick={() => setOverridesText((xs) => [...xs, '{}'])}>+ override 추가</button>
            {!overridesValid && (
              <div className="hint" style={{ color: 'var(--accent-reject)', marginTop: 6 }}>
                JSON 파싱 실패한 override 가 있습니다.
              </div>
            )}

            <h3 style={{ marginTop: 18 }}>2c. prompts (matrix axis)</h3>
            {prompts.map((p, i) => (
              <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                <textarea
                  className="input"
                  rows={2}
                  value={p}
                  onChange={(e) => setPrompts((ps) => ps.map((x, j) => j === i ? e.target.value : x))}
                  placeholder={promptMode === 'subject' ? 'subject 묘사 (variant 의 template 가 합성)' : 'positive prompt…'}
                  style={{ flex: 1 }}
                />
                {prompts.length > 1 && (
                  <button type="button" className="btn"
                          onClick={() => setPrompts((ps) => ps.filter((_, j) => j !== i))}>✕</button>
                )}
              </div>
            ))}
            <button type="button" className="btn" onClick={() => setPrompts((ps) => [...ps, ''])}>+ prompt 추가</button>
            {!prompts.some((p) => p.trim()) && (
              <div className="hint" style={{ color: 'var(--accent-reject)', marginTop: 6 }}>최소 1개 prompt 가 필요합니다.</div>
            )}

            <label className="block" style={{ marginTop: 12 }}>
              <span>prompt_mode</span>
              <select className="input" value={promptMode} onChange={(e) => setPromptMode(e.target.value)}>
                {PROMPT_MODES.map((m) => <option key={m.id} value={m.id}>{m.label} — {m.desc}</option>)}
              </select>
            </label>
            {promptMode !== 'legacy' && (
              <label className="block" style={{ marginTop: 8 }}>
                <span>style_extra (subject 모드 — base_positive 뒤에 추가)</span>
                <input className="input" value={styleExtra} onChange={(e) => setStyleExtra(e.target.value)}/>
              </label>
            )}
            <label className="block" style={{ marginTop: 8 }}>
              <span>negative prompt (common — variant base_negative 와 합쳐짐)</span>
              <textarea className="input" rows={2} value={negative} onChange={(e) => setNegative(e.target.value)}/>
            </label>
          </div>

          {/* RIGHT — seeds + common knobs */}
          <div className="panel-card">
            <h3>2d. seeds</h3>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className={`btn ${seedMode === 'random' ? 'btn-primary' : ''}`} onClick={() => setSeedMode('random')}>random</button>
              <button className={`btn ${seedMode === 'fixed' ? 'btn-primary' : ''}`} onClick={() => setSeedMode('fixed')}>fixed</button>
            </div>
            {seedMode === 'random' ? (
              <label className="block" style={{ marginTop: 10 }}>
                <span>seeds_per_combo (1–256)</span>
                <input className="input" type="number" min={1} max={256}
                       value={seedsPerCombo}
                       onChange={(e) => setSeedsPerCombo(Math.max(1, Math.min(256, Number(e.target.value) || 1)))}/>
              </label>
            ) : (
              <label className="block" style={{ marginTop: 10 }}>
                <span>seeds (csv)</span>
                <input className="input" value={seedsText}
                       onChange={(e) => setSeedsText(e.target.value)}
                       placeholder="42, 1234, 9999"/>
                {hasInvalidFixedSeed && (
                  <div className="hint" style={{ color: 'var(--accent-reject)' }}>유효한 정수 시드가 없습니다.</div>
                )}
              </label>
            )}

            <h3 style={{ marginTop: 18 }}>preset</h3>
            <div style={{ display: 'flex', gap: 6 }}>
              {PRESETS.map((p) => (
                <button key={p.id}
                        className={`btn ${presetId === p.id ? 'btn-primary' : ''}`}
                        onClick={() => applyPreset(p.id)}>{p.label}</button>
              ))}
            </div>

            <h3 style={{ marginTop: 18 }}>variant default override</h3>
            <div className="hint" style={{ marginBottom: 6 }}>
              비워두면 variant.defaults 그대로.
            </div>
            <div className="form-grid">
              <label>
                <span>steps</span>
                <input className="input" type="number" placeholder="(default)"
                       value={stepsOverride} onChange={(e) => setStepsOverride(e.target.value)}/>
              </label>
              <label>
                <span>cfg</span>
                <input className="input" type="number" step="0.1" placeholder="(default)"
                       value={cfgOverride} onChange={(e) => setCfgOverride(e.target.value)}/>
              </label>
              <label style={{ gridColumn: 'span 2' }}>
                <span>sampler</span>
                <input className="input" placeholder="(default)"
                       value={samplerOverride} onChange={(e) => setSamplerOverride(e.target.value)}/>
              </label>
              <label style={{ gridColumn: 'span 2' }}>
                <span>max_colors (output palette cap)</span>
                <input className="input" type="number" placeholder="(default)"
                       value={maxColors} onChange={(e) => setMaxColors(e.target.value)}/>
              </label>
            </div>
          </div>

          <div style={{ gridColumn: 'span 2', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
            <button className="btn" onClick={() => setStep(1)}>← 이전</button>
            <div>
              <span className="chip" style={{ marginRight: 8 }}>예상 <b>{expandedCount}</b>장</span>
              <button className="btn btn-primary" disabled={!step2Valid} onClick={() => setStep(3)}>다음 →</button>
            </div>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="panel-card">
          <h3>3. Review</h3>

          {overLimit && (
            <div className="panel-card" style={{ borderColor: 'var(--accent-warning)', background: 'rgba(255,170,0,0.06)', marginBottom: 12 }}>
              <strong style={{ color: 'var(--accent-warning)' }}>⚠ {expandedCount}장 — 200 초과</strong>
              <div className="hint">디스크/시간이 충분한지 확인하세요. 줄이려면 ← 이전 으로.</div>
            </div>
          )}

          <div className="form-grid">
            <div>
              <dl className="kvlist">
                <dt>workflow</dt><dd>{workflowCategory} / {selectedVariants.join(', ') || '—'}</dd>
                <dt>prompts</dt><dd>{prompts.filter((p) => p.trim()).length}</dd>
                <dt>overrides</dt><dd>{overridesText.length}</dd>
                <dt>seeds</dt><dd>{seedMode === 'fixed' ? `fixed: ${seedList?.length || 0}` : `random × ${seedsPerCombo}`}</dd>
                <dt>총 task</dt><dd><b>{expandedCount}</b></dd>
              </dl>
            </div>
            <div>
              <button type="button" className="btn"
                      onClick={() => setShowJsonPreview((v) => !v)}>
                JSON 미리보기 {showJsonPreview ? '숨김' : '보기'}
              </button>
            </div>
          </div>

          {showJsonPreview && (
            <pre className="code-block" style={{ marginTop: 12, maxHeight: 320, overflow: 'auto' }}>
              {JSON.stringify(specPayload, null, 2)}
            </pre>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 }}>
            <button className="btn" onClick={() => setStep(2)}>← 이전</button>
            <button className="btn btn-primary" onClick={submit} disabled={submitting || !step2Valid}>
              {submitting ? '생성 중…' : `batch 생성 (${expandedCount}장)`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

window.BatchNew = BatchNew;

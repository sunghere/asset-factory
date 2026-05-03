/* Manual — single ComfyUI workflow generation (POST /api/workflows/generate).
   batch wizard 의 곱집합이 필요 없는 1회성 호출용. variant default 그대로
   호출하거나 prompt + 소수의 params override 만 바꿔서 빠르게 트라이.

   Backed by WorkflowGenerateRequest. candidates_total > 1 이면 cherry-pick
   N 슬롯이 같이 만들어진다. */

const { useState, useMemo, useEffect } = React;

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

const PROMPT_MODES = [
  { id: 'auto',    label: 'auto' },
  { id: 'legacy',  label: 'legacy' },
  { id: 'subject', label: 'subject' },
];

function _safeParseJson(text, fallback) {
  if (!text || !text.trim()) return fallback;
  try { return JSON.parse(text); } catch { return null; }
}

function Manual() {
  const toasts = window.useToasts();
  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);
  const catalog = window.useAsync(() => window.api.comfyuiCatalog().catch(() => null), []);

  const [project, setProject] = useState('default-project');
  const [assetKey, setAssetKey] = useState('');
  const [category, setCategory] = useState('character');

  const [workflowCategory, setWorkflowCategory] = useState('');
  const [workflowVariant, setWorkflowVariant] = useState('');

  const [prompt, setPrompt] = useState('');
  const [promptMode, setPromptMode] = useState('auto');
  const [subject, setSubject] = useState('');
  const [styleExtra, setStyleExtra] = useState('');
  const [negative, setNegative] = useState('');
  const [paramsText, setParamsText] = useState('{}');

  const [seedText, setSeedText] = useState('');
  const [candidatesTotal, setCandidatesTotal] = useState(1);

  const [stepsOverride, setStepsOverride] = useState('');
  const [cfgOverride, setCfgOverride] = useState('');
  const [samplerOverride, setSamplerOverride] = useState('');
  const [maxColors, setMaxColors] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);

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

  // 첫 카탈로그 로드 시 prefill / 첫 항목 자동 선택.
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
      const primary = vs.find((v) => v.variantName === pf.variant_name)
        || vs.find((v) => v.variantName === pf.workflow_variant)
        || vs.find((v) => /pixel_alpha|primary/i.test(v.variantName))
        || vs[0];
      if (primary) setWorkflowVariant(primary.variantName);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowCategories.join('|')]);

  // 프로젝트 목록이 도착했는데 현재 state 값이 옵션에 없으면 첫 옵션으로 보정.
  // 이전 버그: state default 가 'default-project' 인데 dropdown 옵션이 다른
  // ID 들이라, 사용자가 dropdown 을 직접 클릭해 변경하지 않으면 submit 시
  // payload.project='default-project' 로 새서 결과가 엉뚱한 프로젝트 (혹은
  // 비어 있는 default) 에 저장된다.
  useEffect(() => {
    const items = projects.data?.items;
    if (!items?.length) return;
    const ids = items.map((p) => typeof p === 'string' ? p : (p.id ?? p.name ?? String(p)));
    if (!ids.includes(project)) {
      setProject(ids[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects.data]);

  const variantsHere = variantsByCategory[workflowCategory] || [];
  const paramsParsed = _safeParseJson(paramsText, {});
  const paramsValid = paramsParsed !== null;

  const seedParsed = useMemo(() => {
    if (!seedText.trim()) return null;
    const n = parseInt(seedText, 10);
    return Number.isInteger(n) ? n : 'invalid';
  }, [seedText]);

  const formValid =
    project.trim() &&
    assetKey.trim() &&
    workflowCategory &&
    workflowVariant &&
    paramsValid &&
    seedParsed !== 'invalid' &&
    (promptMode === 'subject' ? subject.trim() : prompt.trim() || subject.trim());

  const payload = useMemo(() => ({
    project: project.trim(),
    asset_key: assetKey.trim(),
    category: category.trim() || 'character',
    workflow_category: workflowCategory,
    workflow_variant: workflowVariant,
    prompt: prompt.trim(),
    negative_prompt: negative.trim() || null,
    prompt_mode: promptMode,
    subject: subject.trim() || null,
    style_extra: styleExtra.trim() || null,
    workflow_params: paramsParsed || {},
    seed: seedParsed === null || seedParsed === 'invalid' ? null : seedParsed,
    candidates_total: Math.max(1, Math.min(16, Number(candidatesTotal) || 1)),
    steps: stepsOverride === '' ? null : Math.max(1, Math.min(200, Number(stepsOverride))),
    cfg: cfgOverride === '' ? null : Math.max(0, Math.min(30, Number(cfgOverride))),
    sampler: samplerOverride.trim() || null,
    max_colors: maxColors === '' ? null : Math.max(1, Math.min(256, Number(maxColors))),
    max_retries: 3,
    expected_size: null,
  }), [
    project, assetKey, category, workflowCategory, workflowVariant,
    prompt, negative, promptMode, subject, styleExtra, paramsParsed,
    seedParsed, candidatesTotal, stepsOverride, cfgOverride,
    samplerOverride, maxColors,
  ]);

  async function submit() {
    setSubmitting(true);
    setResult(null);
    setJobStatus(null);
    try {
      const resp = await window.api.workflowsGenerate(payload);
      setResult(resp);
      toasts.push({
        kind: 'success',
        message: `요청됨 · job_id=${resp.job_id?.slice(0, 8)}… (${resp.candidates_total}장)`,
        ttl: 6000,
      });
    } catch (e) {
      toasts.push({ kind: 'error', message: '요청 실패: ' + (e.message || e), ttl: 8000 });
    } finally {
      setSubmitting(false);
    }
  }

  // job 상태 폴링 — submit 후 완료까지.
  useEffect(() => {
    if (!result?.job_id) return;
    let cancelled = false;
    let timer;
    async function tick() {
      if (cancelled) return;
      try {
        const j = await window.api.getJob(result.job_id);
        setJobStatus(j);
        if (j.status === 'done' || j.status === 'failed' || j.status === 'cancelled') {
          return;
        }
      } catch { /* ignore */ }
      timer = setTimeout(tick, 2000);
    }
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [result?.job_id]);

  return (
    <div>
      <window.PageToolbar
        info={{
          title: 'manual · single',
          text: 'ComfyUI 워크플로우 1회 호출. candidates_total > 1 이면 cherry-pick 흐름. 곱집합 없이 단일 variant + 단일 params 만.',
        }}
      />

      <div style={{ display: 'grid', gap: 14, gridTemplateColumns: '2fr 1fr' }}>
        {/* LEFT: workflow + prompt */}
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
              <span>category</span>
              <input className="input" value={category} onChange={(e) => setCategory(e.target.value)}/>
            </label>
            <label style={{ gridColumn: 'span 2' }}>
              <span>asset_key</span>
              <input className="input" value={assetKey} onChange={(e) => setAssetKey(e.target.value)} placeholder="e.g. marine_v2_idle"/>
            </label>
          </div>

          <h3 style={{ marginTop: 18 }}>2. Workflow</h3>
          {workflowCategories.length === 0 ? (
            <div className="hint">ComfyUI 카탈로그를 불러올 수 없습니다.</div>
          ) : (
            <div className="form-grid">
              <label>
                <span>workflow_category</span>
                <select className="input" value={workflowCategory}
                        onChange={(e) => { setWorkflowCategory(e.target.value); setWorkflowVariant(''); }}>
                  {workflowCategories.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label>
                <span>workflow_variant</span>
                <select className="input" value={workflowVariant}
                        onChange={(e) => setWorkflowVariant(e.target.value)}>
                  <option value="">— 선택 —</option>
                  {variantsHere.map((v) => (
                    <option key={v.id} value={v.variantName}>{v.variantName}</option>
                  ))}
                </select>
              </label>
            </div>
          )}

          <h3 style={{ marginTop: 18 }}>3. Prompt</h3>
          <label className="block">
            <span>prompt_mode</span>
            <select className="input" value={promptMode} onChange={(e) => setPromptMode(e.target.value)}>
              {PROMPT_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          {promptMode === 'subject' ? (
            <label className="block" style={{ marginTop: 8 }}>
              <span>subject (variant template 가 합성)</span>
              <textarea className="input" rows={2} value={subject} onChange={(e) => setSubject(e.target.value)}/>
            </label>
          ) : (
            <label className="block" style={{ marginTop: 8 }}>
              <span>prompt</span>
              <textarea className="input" rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)}
                        placeholder="positive prompt…"/>
            </label>
          )}
          {promptMode !== 'legacy' && (
            <label className="block" style={{ marginTop: 8 }}>
              <span>style_extra (선택)</span>
              <input className="input" value={styleExtra} onChange={(e) => setStyleExtra(e.target.value)}/>
            </label>
          )}
          <label className="block" style={{ marginTop: 8 }}>
            <span>negative prompt</span>
            <textarea className="input" rows={2} value={negative} onChange={(e) => setNegative(e.target.value)}/>
          </label>

          <h3 style={{ marginTop: 18 }}>4. workflow_params (선택)</h3>
          <div className="hint" style={{ marginBottom: 6 }}>
            <code>patch_workflow</code> 인자 dict. 빈 <code>{'{}'}</code> 면 variant default 그대로.
          </div>
          <textarea className="input" rows={3} value={paramsText}
                    style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: paramsValid ? undefined : 'var(--accent-reject)' }}
                    onChange={(e) => setParamsText(e.target.value)}/>
          {!paramsValid && (
            <div className="hint" style={{ color: 'var(--accent-reject)' }}>JSON 파싱 실패.</div>
          )}
        </div>

        {/* RIGHT: seeds + override + submit */}
        <div className="panel-card">
          <h3>5. Seeds</h3>
          <label className="block">
            <span>seed (비우면 random)</span>
            <input className="input" value={seedText}
                   onChange={(e) => setSeedText(e.target.value)} placeholder="e.g. 42"/>
            {seedParsed === 'invalid' && (
              <div className="hint" style={{ color: 'var(--accent-reject)' }}>정수가 아닙니다.</div>
            )}
          </label>
          <label className="block" style={{ marginTop: 8 }}>
            <span>candidates_total (1–16)</span>
            <input className="input" type="number" min={1} max={16} value={candidatesTotal}
                   onChange={(e) => setCandidatesTotal(Math.max(1, Math.min(16, Number(e.target.value) || 1)))}/>
          </label>
          <div className="hint">{'>'} 1 이면 cherry-pick 슬롯 생성.</div>

          <h3 style={{ marginTop: 18 }}>6. Variant default override</h3>
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
              <span>max_colors</span>
              <input className="input" type="number" placeholder="(default)"
                     value={maxColors} onChange={(e) => setMaxColors(e.target.value)}/>
            </label>
          </div>

          <button className="btn btn-primary" style={{ marginTop: 18, width: '100%' }}
                  disabled={!formValid || submitting} onClick={submit}>
            {submitting ? '요청 중…' : `생성 (${payload.candidates_total}장)`}
          </button>
        </div>
      </div>

      {result && (
        <div className="panel-card" style={{ marginTop: 14 }}>
          <h3>결과</h3>
          <dl className="kvlist">
            <dt>batch_id</dt>
            <dd style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{result.batch_id}</dd>
            <dt>job_id</dt>
            <dd style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{result.job_id}</dd>
            <dt>workflow</dt>
            <dd>{result.workflow_category} / {result.workflow_variant}</dd>
            <dt>candidates</dt>
            <dd>{result.candidates_total}</dd>
            {result.primary_output && (
              <>
                <dt>primary_output</dt>
                <dd>{result.primary_output}</dd>
              </>
            )}
            {jobStatus && (
              <>
                <dt>status</dt>
                <dd>
                  <span className={`pill ${jobStatus.status === 'done' ? 'active' : ''}`}>
                    {jobStatus.status}
                  </span>
                </dd>
              </>
            )}
          </dl>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            {result.batch_id && (
              <a className="btn"
                 href={`/app/batches/${result.batch_id}`}
                 onClick={(e) => { e.preventDefault(); window.navigate(`/batches/${result.batch_id}`); }}>
                batch 상세
              </a>
            )}
            {result.batch_id && jobStatus?.status === 'done' && (
              <a className="btn btn-primary"
                 href={`/app/cherry-pick/${result.batch_id}`}
                 onClick={(e) => { e.preventDefault(); window.navigate(`/cherry-pick/${result.batch_id}`); }}>
                cherry-pick 열기
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

window.Manual = Manual;

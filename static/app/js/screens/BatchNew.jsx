/* BatchNew — 3-step wizard for POST /api/batches.
   Step 1: identity (project + asset_key + category)
   Step 2: spec    (prompts, models, loras MATRIX, seeds, common SD params)
   Step 3: review  (JSON preview + 200+ 경고 + confirm → fire request)
   Backed by DesignBatchRequest (loras: LoraSpec[][]). 곱집합:
     prompts × models × lora_groups × seeds. */

const { useState, useMemo } = React;

const PRESETS = [
  { id: 'fast',     label: 'fast',     steps: 22, cfg: 6.5, seeds_per_combo: 1 },
  { id: 'standard', label: 'standard', steps: 28, cfg: 7.0, seeds_per_combo: 2 },
  { id: 'final',    label: 'final',    steps: 36, cfg: 7.5, seeds_per_combo: 4 },
];
const SAMPLERS = ['DPM++ 2M', 'DPM++ 2M Karras', 'Euler a', 'Euler', 'DDIM', 'DPM++ SDE Karras'];

function BatchNew() {
  const toasts = window.useToasts();
  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);
  const models = window.useAsync(() => window.api.models().catch(() => null), []);
  const loras = window.useAsync(() => window.api.loras().catch(() => null), []);

  const [step, setStep] = useState(1);

  const [project, setProject] = useState('default-project');
  const [assetKey, setAssetKey] = useState('');
  const [category, setCategory] = useState('character');

  const [prompts, setPrompts] = useState(['']);
  const [negative, setNegative] = useState('');
  const [selectedModels, setSelectedModels] = useState([]);
  // loraGroups: [[{name, weight}, ...], ...] — 각 행이 하나의 조합.
  // 빈 배열 [] 은 "LoRA 없음" 조합. 사용자는 여러 조합을 곱집합으로 실험.
  const [loraGroups, setLoraGroups] = useState([[]]);
  const [seedMode, setSeedMode] = useState('random');
  const [seedsText, setSeedsText] = useState('');
  const [seedsPerCombo, setSeedsPerCombo] = useState(2);
  const [presetId, setPresetId] = useState('standard');
  const [steps, setSteps] = useState(28);
  const [cfg, setCfg] = useState(7.0);
  const [sampler, setSampler] = useState('DPM++ 2M');
  const [width, setWidth] = useState('');
  const [height, setHeight] = useState('');
  const [maxColors, setMaxColors] = useState(32);

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);

  const seedList = useMemo(() => {
    if (seedMode !== 'fixed') return null;
    return seedsText.split(/[,\s]+/).map((s) => parseInt(s, 10)).filter((n) => Number.isInteger(n));
  }, [seedMode, seedsText]);

  const expandedCount = useMemo(() => {
    const p = prompts.filter((s) => s.trim()).length;
    const m = selectedModels.length;
    const l = Math.max(1, loraGroups.length);
    const s = seedMode === 'fixed' ? Math.max(1, (seedList || []).length) : Math.max(1, seedsPerCombo);
    return p * m * l * s;
  }, [prompts, selectedModels, loraGroups, seedMode, seedList, seedsPerCombo]);

  function applyPreset(id) {
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    setPresetId(id); setSteps(p.steps); setCfg(p.cfg); setSeedsPerCombo(p.seeds_per_combo);
  }

  const step1Valid = project.trim() && assetKey.trim() && category.trim();
  const step2Valid = prompts.some((p) => p.trim()) && selectedModels.length > 0 && expandedCount > 0;

  // 실제 전송될 spec — step 3 JSON preview + submit 공용.
  const specPayload = useMemo(() => ({
    asset_key: assetKey.trim(),
    project: project.trim(),
    category: category.trim(),
    prompts: prompts.map((p) => p.trim()).filter(Boolean),
    models: selectedModels,
    loras: loraGroups.map((grp) => grp.map((l) => ({ name: l.name, weight: Number(l.weight) || 0.7 }))),
    seeds: seedMode === 'fixed' ? seedList : null,
    seeds_per_combo: seedsPerCombo,
    common: {
      steps,
      cfg,
      sampler: sampler.trim() || 'DPM++ 2M',
      width: width ? Math.max(64, Math.min(2048, Number(width))) : null,
      height: height ? Math.max(64, Math.min(2048, Number(height))) : null,
      negative_prompt: negative.trim() || null,
      max_colors: Math.max(1, Math.min(256, Number(maxColors) || 32)),
      max_retries: 3,
    },
  }), [
    assetKey, project, category, prompts, selectedModels, loraGroups,
    seedMode, seedList, seedsPerCombo, steps, cfg, sampler, width, height,
    negative, maxColors,
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

  const modelItems = models.data?.items || [];
  const loraItems = loras.data?.items || [];

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
          text: '3-step 마법사. DesignBatchRequest 스키마 그대로. prompts × models × lora_groups × seeds 곱집합 → 200장 초과 시 경고. review 에서 최종 JSON 미리보기 후 POST.',
        }}
      />

      <div className="wizard-steps">
        <div className={`step ${step === 1 ? 'active' : ''} ${step > 1 ? 'done' : ''}`}>1 · identity</div>
        <div className={`step ${step === 2 ? 'active' : ''} ${step > 2 ? 'done' : ''}`}>2 · spec</div>
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
              <a
                className="btn btn-primary"
                href={`/app/cherry-pick/${result.batch_id}`}
                onClick={(e) => { e.preventDefault(); window.navigate(`/cherry-pick/${result.batch_id}`); }}
              >cherry-pick 열기</a>
            </div>
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="panel-card">
          <h3>1. 대상 에셋</h3>
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
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
            <button className="btn btn-primary" disabled={!step1Valid} onClick={() => setStep(2)}>
              다음 →
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div style={{ display: 'grid', gap: 14, gridTemplateColumns: '2fr 1fr' }}>
          <div className="panel-card">
            <h3>2a. prompts</h3>
            {prompts.map((p, i) => (
              <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                <textarea
                  className="input"
                  rows={2}
                  value={p}
                  onChange={(e) => setPrompts((ps) => ps.map((x, j) => j === i ? e.target.value : x))}
                  placeholder="positive prompt…"
                  style={{ flex: 1 }}
                />
                {prompts.length > 1 && (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setPrompts((ps) => ps.filter((_, j) => j !== i))}
                  >✕</button>
                )}
              </div>
            ))}
            <button type="button" className="btn" onClick={() => setPrompts((ps) => [...ps, ''])}>+ prompt 추가</button>

            <label className="block" style={{ marginTop: 14 }}>
              <span>negative prompt (common)</span>
              <textarea className="input" rows={2} value={negative} onChange={(e) => setNegative(e.target.value)}/>
            </label>

            <h3 style={{ marginTop: 18 }}>2b. models</h3>
            {modelItems.length === 0 ? (
              <div className="hint">모델 카탈로그를 불러올 수 없습니다. /system에서 SD 연결을 확인하세요.</div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {modelItems.map((m) => {
                  const name = m.name;
                  const sel = selectedModels.includes(name);
                  return (
                    <span
                      key={name}
                      className={`pill ${sel ? 'active' : ''}`}
                      onClick={() => setSelectedModels((xs) => sel ? xs.filter((x) => x !== name) : [...xs, name])}
                    >{name}</span>
                  );
                })}
              </div>
            )}

            <h3 style={{ marginTop: 18 }}>2c. loras matrix</h3>
            <div style={{ color: 'var(--text-faint)', fontSize: 11, fontFamily: 'var(--font-mono)', marginBottom: 8 }}>
              각 행 = 하나의 LoRA 조합 (빈 행 = "LoRA 없음"). 여러 행이면 곱집합 늘어남.
            </div>
            <LoraMatrix
              groups={loraGroups}
              setGroups={setLoraGroups}
              catalog={loraItems}
            />
          </div>

          <div className="panel-card" style={{ alignSelf: 'start' }}>
            <h3>2d. seeds</h3>
            <div className="form-grid">
              <label style={{ gridColumn: 'span 2' }}>
                <span>seed 방식</span>
                <select className="input" value={seedMode} onChange={(e) => setSeedMode(e.target.value)}>
                  <option value="random">random (seeds_per_combo)</option>
                  <option value="fixed">fixed list</option>
                </select>
              </label>
              {seedMode === 'random' ? (
                <label><span>seeds/combo</span>
                  <input
                    className="input" type="number" min={1} max={64}
                    value={seedsPerCombo}
                    onChange={(e) => setSeedsPerCombo(Math.max(1, Math.min(64, parseInt(e.target.value || 1))))}
                  />
                </label>
              ) : (
                <label style={{ gridColumn: 'span 2' }}>
                  <span>seeds (쉼표/공백)</span>
                  <input className="input" value={seedsText} onChange={(e) => setSeedsText(e.target.value)} placeholder="42, 123, 999"/>
                </label>
              )}
            </div>

            <h3 style={{ marginTop: 16 }}>preset</h3>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {PRESETS.map((p) => (
                <span key={p.id} className={`pill ${presetId === p.id ? 'active' : ''}`} onClick={() => applyPreset(p.id)}>
                  {p.label}
                </span>
              ))}
            </div>

            <h3 style={{ marginTop: 16 }}>2e. common SD params</h3>
            <div className="form-grid">
              <label><span>steps</span>
                <input className="input" type="number" value={steps} min={1} max={200}
                       onChange={(e) => setSteps(Math.max(1, Math.min(200, parseInt(e.target.value || 1))))}/>
              </label>
              <label><span>cfg</span>
                <input className="input" type="number" step="0.1" value={cfg} min={0} max={30}
                       onChange={(e) => setCfg(parseFloat(e.target.value || 0))}/>
              </label>
              <label style={{ gridColumn: 'span 2' }}>
                <span>sampler</span>
                <select className="input" value={sampler} onChange={(e) => setSampler(e.target.value)}>
                  {SAMPLERS.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <label><span>width</span>
                <input className="input" type="number" placeholder="auto" value={width}
                       onChange={(e) => setWidth(e.target.value)} min={64} max={2048} step={8}/>
              </label>
              <label><span>height</span>
                <input className="input" type="number" placeholder="auto" value={height}
                       onChange={(e) => setHeight(e.target.value)} min={64} max={2048} step={8}/>
              </label>
              <label style={{ gridColumn: 'span 2' }}><span>max_colors (검증)</span>
                <input className="input" type="number" value={maxColors} min={1} max={256}
                       onChange={(e) => setMaxColors(parseInt(e.target.value || 32))}/>
              </label>
            </div>

            <div style={{ marginTop: 14, padding: 12, background: 'var(--bg-elev-3)', borderRadius: 6, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
              예상 후보: <b style={{ color: overLimit ? 'var(--accent-warning)' : 'var(--accent-pick)' }}>{expandedCount}</b>
              <div style={{ color: 'var(--text-faint)', fontSize: 11, marginTop: 4 }}>
                prompts × models × lora_groups × seeds
              </div>
              {overLimit && (
                <div style={{ color: 'var(--accent-warning)', fontSize: 11, marginTop: 6 }}>
                  ⚠ 200장 이상입니다. step 3 에서 재확인.
                </div>
              )}
            </div>
          </div>

          <div style={{ gridColumn: 'span 2', display: 'flex', justifyContent: 'space-between' }}>
            <button className="btn" onClick={() => setStep(1)}>← 이전</button>
            <button className="btn btn-primary" disabled={!step2Valid} onClick={() => setStep(3)}>
              검토 →
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div className="panel-card">
            <h3>3. 확인</h3>
            <dl className="meta-block">
              <dt>project</dt><dd>{project}</dd>
              <dt>asset_key</dt><dd>{assetKey}</dd>
              <dt>category</dt><dd>{category}</dd>
              <dt>prompts</dt><dd>{prompts.filter((p) => p.trim()).length}개</dd>
              <dt>models</dt><dd>{selectedModels.join(', ') || '—'}</dd>
              <dt>lora_groups</dt>
              <dd>
                {loraGroups.length === 0 ? '—' : (
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {loraGroups.map((g, i) => (
                      <li key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                        {g.length === 0 ? '(none)' : g.map((l) => `${l.name}:${l.weight}`).join(', ')}
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
              <dt>seed 방식</dt><dd>{seedMode === 'fixed' ? `fixed (${(seedList || []).length}개)` : `random × ${seedsPerCombo}`}</dd>
              <dt>steps / cfg / sampler</dt><dd>{steps} / {cfg} / {sampler}</dd>
              <dt>w × h</dt><dd>{(width || 'auto')} × {(height || 'auto')}</dd>
              <dt>max_colors</dt><dd>{maxColors}</dd>
              <dt>예상 후보</dt>
              <dd style={{ color: overLimit ? 'var(--accent-warning)' : 'var(--accent-pick)', fontWeight: 600 }}>
                {expandedCount}장 {overLimit ? '⚠' : ''}
              </dd>
            </dl>
            {overLimit && (
              <div style={{
                marginTop: 12, padding: 10,
                border: '1px solid var(--accent-warning)', borderRadius: 4,
                color: 'var(--accent-warning)', fontFamily: 'var(--font-mono)', fontSize: 12,
              }}>
                ⚠ 작업이 많습니다 ({expandedCount}장). 정말 enqueue 할지 한번 더 확인하세요.
                GPU/디스크 소모가 큽니다.
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
              <button className="btn" onClick={() => setStep(2)} disabled={submitting}>← 이전</button>
              <button
                className={`btn ${overLimit ? '' : 'btn-primary'}`}
                style={overLimit ? { background: 'var(--accent-warning)', color: '#000' } : undefined}
                onClick={submit}
                disabled={submitting || expandedCount === 0}
              >
                {submitting ? '… 생성 중' : `▶ 배치 생성 (${expandedCount})${overLimit ? ' · 확인' : ''}`}
              </button>
            </div>
          </div>

          <div className="panel-card" style={{ padding: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--border-subtle)' }}>
              <h3 style={{ margin: 0 }}>JSON preview · POST /api/batches</h3>
              <button
                className="btn"
                onClick={() => {
                  try {
                    navigator.clipboard.writeText(JSON.stringify(specPayload, null, 2));
                    toasts.push({ kind: 'info', message: 'JSON 복사됨', ttl: 1500 });
                  } catch (_) { /* noop */ }
                }}
              >copy</button>
            </div>
            <pre style={{
              margin: 0, padding: 14,
              fontFamily: 'var(--font-mono)', fontSize: 11,
              color: 'var(--text-secondary)',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              maxHeight: 520, overflow: 'auto',
              background: 'transparent',
            }}>
              {JSON.stringify(specPayload, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function LoraMatrix({ groups, setGroups, catalog }) {
  // 카탈로그가 없어도 그룹 관리는 가능하지만, 이름을 직접 입력해야 해서 UX 제한적.
  const catalogByName = useMemo(() => {
    const m = {};
    for (const l of (catalog || [])) m[l.name] = l;
    return m;
  }, [catalog]);

  function addGroup() {
    setGroups((gs) => [...gs, []]);
  }
  function removeGroup(idx) {
    setGroups((gs) => gs.length <= 1 ? [[]] : gs.filter((_, i) => i !== idx));
  }
  function addLoraToGroup(gi, name) {
    if (!name) return;
    const def = catalogByName[name]?.weight_default ?? 0.7;
    setGroups((gs) => gs.map((grp, i) => {
      if (i !== gi) return grp;
      if (grp.some((l) => l.name === name)) return grp;
      return [...grp, { name, weight: def }];
    }));
  }
  function updateWeight(gi, li, w) {
    setGroups((gs) => gs.map((grp, i) =>
      i !== gi ? grp : grp.map((l, j) => j === li ? { ...l, weight: w } : l)
    ));
  }
  function removeLora(gi, li) {
    setGroups((gs) => gs.map((grp, i) =>
      i !== gi ? grp : grp.filter((_, j) => j !== li)
    ));
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {groups.map((grp, gi) => (
        <div key={gi} style={{
          border: '1px solid var(--border-subtle)', borderRadius: 4, padding: 8,
          display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', minWidth: 42 }}>
            #{gi + 1}
          </span>
          {grp.length === 0 && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
              (LoRA 없음)
            </span>
          )}
          {grp.map((l, li) => (
            <span key={l.name} className="pill active" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              {l.name}
              <input
                type="number" step="0.05" min="-2" max="2"
                value={l.weight}
                onChange={(e) => updateWeight(gi, li, Number(e.target.value))}
                style={{ width: 52, background: 'transparent', border: 'none', color: 'inherit', fontFamily: 'var(--font-mono)', fontSize: 11 }}
              />
              <button
                type="button"
                onClick={() => removeLora(gi, li)}
                style={{ background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, marginLeft: 2 }}
                title="제거"
              >✕</button>
            </span>
          ))}
          <select
            className="input"
            value=""
            onChange={(e) => { addLoraToGroup(gi, e.target.value); e.target.value = ''; }}
            style={{ width: 180 }}
          >
            <option value="">+ LoRA 추가…</option>
            {(catalog || []).map((l) => (
              <option key={l.name} value={l.name}>{l.name}</option>
            ))}
          </select>
          <div style={{ flex: 1 }}/>
          <button className="btn" type="button" onClick={() => removeGroup(gi)} title="행 삭제">✕</button>
        </div>
      ))}
      <button type="button" className="btn" onClick={addGroup} style={{ alignSelf: 'flex-start' }}>
        + lora 조합 추가
      </button>
    </div>
  );
}

window.BatchNew = BatchNew;

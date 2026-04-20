/* BatchNew — 3-step wizard for POST /api/batches.
   Step 1: identity (project + asset_key + category)
   Step 2: spec    (prompts, models, loras, seeds)
   Step 3: review  (summary + confirm → fire request)
   Backed by the same DesignBatchRequest schema as Regen.jsx; this screen
   trades power for a more guided flow (one prompt at a time, lora picker
   with weights, explicit review step before disk/GPU time is spent). */

const { useState, useMemo } = React;

const PRESETS = [
  { id: 'fast',     label: 'fast',     steps: 22, cfg: 6.5, seeds_per_combo: 1 },
  { id: 'standard', label: 'standard', steps: 28, cfg: 7.0, seeds_per_combo: 2 },
  { id: 'final',    label: 'final',    steps: 36, cfg: 7.5, seeds_per_combo: 4 },
];

function BatchNew() {
  const toasts = window.useToasts();
  const projects = window.useAsync(() => window.api.listProjects().catch(() => []), []);
  const models = window.useAsync(() => window.api.models().catch(() => null), []);
  const loras = window.useAsync(() => window.api.loras().catch(() => null), []);

  const [step, setStep] = useState(1);

  // Step 1 — identity
  const [project, setProject] = useState('default-project');
  const [assetKey, setAssetKey] = useState('');
  const [category, setCategory] = useState('character');

  // Step 2 — spec
  const [prompts, setPrompts] = useState(['']);
  const [negative, setNegative] = useState('');
  const [selectedModels, setSelectedModels] = useState([]);
  const [loraSel, setLoraSel] = useState({}); // {name: weight}
  const [seedMode, setSeedMode] = useState('random'); // 'random' | 'fixed'
  const [seedsText, setSeedsText] = useState('');
  const [seedsPerCombo, setSeedsPerCombo] = useState(2);
  const [presetId, setPresetId] = useState('standard');
  const [steps, setSteps] = useState(28);
  const [cfg, setCfg] = useState(7.0);

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);

  const loraGroup = useMemo(() => {
    const arr = Object.entries(loraSel).map(([name, weight]) => ({ name, weight: Number(weight) || 0.7 }));
    return arr.length ? [arr] : [];
  }, [loraSel]);

  const seedList = useMemo(() => {
    if (seedMode !== 'fixed') return null;
    return seedsText.split(/[,\s]+/).map((s) => parseInt(s, 10)).filter((n) => Number.isInteger(n));
  }, [seedMode, seedsText]);

  const expandedCount = useMemo(() => {
    const p = prompts.filter((s) => s.trim()).length;
    const m = selectedModels.length;
    const l = Math.max(1, loraGroup.length);
    const s = seedMode === 'fixed' ? Math.max(1, (seedList || []).length) : Math.max(1, seedsPerCombo);
    return p * m * l * s;
  }, [prompts, selectedModels, loraGroup, seedMode, seedList, seedsPerCombo]);

  function applyPreset(id) {
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    setPresetId(id); setSteps(p.steps); setCfg(p.cfg); setSeedsPerCombo(p.seeds_per_combo);
  }

  // Validation gates per step
  const step1Valid = project.trim() && assetKey.trim() && category.trim();
  const step2Valid = prompts.some((p) => p.trim()) && selectedModels.length > 0 && expandedCount > 0;

  async function submit() {
    setSubmitting(true);
    try {
      const spec = {
        asset_key: assetKey.trim(),
        project: project.trim(),
        category: category.trim(),
        prompts: prompts.map((p) => p.trim()).filter(Boolean),
        models: selectedModels,
        loras: loraGroup,
        seeds: seedMode === 'fixed' ? seedList : null,
        seeds_per_combo: seedsPerCombo,
        common: {
          steps, cfg,
          sampler: 'DPM++ 2M',
          negative_prompt: negative.trim() || null,
          max_colors: 32,
          max_retries: 3,
        },
      };
      const resp = await window.api.createDesignBatch(spec);
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

  return (
    <div>
      <window.PageToolbar
        left={
          <a href="/app/batches" onClick={(e) => { e.preventDefault(); window.navigate('/batches'); }}
             style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>← batches</a>
        }
        right={<span className="chip">예상 <b>{expandedCount}</b>장</span>}
        info={{
          title: 'batch · new',
          text: '3-step 마법사. Regen.jsx 와 동일한 DesignBatchRequest 스키마를 사용하되, prompt/model/lora 를 개별적으로 쌓고 review 에서 확정 후 POST. 전력/디스크 소모가 큰 작업이라 중간 게이트가 있습니다.',
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
              {projects.data && projects.data.length > 0 ? (
                <select className="input" value={project} onChange={(e) => setProject(e.target.value)}>
                  {projects.data.map((p) => {
                    const id = typeof p === 'string' ? p : (p.id ?? p.name ?? String(p));
                    return <option key={id} value={id}>{id}</option>;
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
              <span>negative prompt</span>
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

            <h3 style={{ marginTop: 18 }}>2c. loras (optional)</h3>
            {loraItems.length === 0 ? (
              <div className="hint">LoRA 없음.</div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {loraItems.map((l) => {
                  const name = l.name;
                  const sel = loraSel[name] != null;
                  return (
                    <span
                      key={name}
                      className={`pill ${sel ? 'active' : ''}`}
                      onClick={() => setLoraSel((s) => {
                        const copy = { ...s };
                        if (sel) delete copy[name];
                        else copy[name] = (l.weight_default ?? 0.7);
                        return copy;
                      })}
                    >{name}{sel && <b style={{ marginLeft: 4 }}>:{loraSel[name]}</b>}</span>
                  );
                })}
              </div>
            )}
          </div>

          <div className="panel-card" style={{ alignSelf: 'start' }}>
            <h3>2d. seeds + preset</h3>
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
            <div className="form-grid" style={{ marginTop: 12 }}>
              <label><span>steps</span>
                <input className="input" type="number" value={steps} min={1} max={200}
                       onChange={(e) => setSteps(Math.max(1, Math.min(200, parseInt(e.target.value || 1))))}/>
              </label>
              <label><span>cfg</span>
                <input className="input" type="number" step="0.1" value={cfg} min={0} max={30}
                       onChange={(e) => setCfg(parseFloat(e.target.value || 0))}/>
              </label>
            </div>

            <div style={{ marginTop: 14, padding: 12, background: 'var(--bg-elev-3)', borderRadius: 6, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
              예상 후보: <b style={{ color: 'var(--accent-pick)' }}>{expandedCount}</b>
              <div style={{ color: 'var(--text-faint)', fontSize: 11, marginTop: 4 }}>
                prompts × models × loras × seeds
              </div>
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
        <div className="panel-card">
          <h3>3. 확인</h3>
          <dl className="meta-block">
            <dt>project</dt><dd>{project}</dd>
            <dt>asset_key</dt><dd>{assetKey}</dd>
            <dt>category</dt><dd>{category}</dd>
            <dt>prompts</dt><dd>{prompts.filter((p) => p.trim()).length}개</dd>
            <dt>models</dt><dd>{selectedModels.join(', ') || '—'}</dd>
            <dt>loras</dt><dd>{Object.keys(loraSel).length === 0 ? '—' : Object.entries(loraSel).map(([n, w]) => `${n}:${w}`).join(', ')}</dd>
            <dt>seed 방식</dt><dd>{seedMode === 'fixed' ? `fixed (${(seedList || []).length}개)` : `random × ${seedsPerCombo}`}</dd>
            <dt>steps / cfg</dt><dd>{steps} / {cfg}</dd>
            <dt>예상 후보</dt>
            <dd style={{ color: 'var(--accent-pick)', fontWeight: 600 }}>{expandedCount}장</dd>
          </dl>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
            <button className="btn" onClick={() => setStep(2)} disabled={submitting}>← 이전</button>
            <button className="btn btn-primary" onClick={submit} disabled={submitting || expandedCount === 0}>
              {submitting ? '… 생성 중' : `▶ 배치 생성 (${expandedCount})`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

window.BatchNew = BatchNew;

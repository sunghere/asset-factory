/* Regen — manual batch creation form (POST /api/batches).

   Maps directly to the DesignBatchRequest schema in server.py. Models / LoRAs
   come from /api/sd/catalog. Designed for the operator who wants to fire off
   a quick exploration batch without writing JSON by hand. */

const { useState, useMemo } = React;

const PRESETS = [
  { id: 'fast',     label: 'fast (12)',  steps: 22, cfg: 6.5, seeds_per_combo: 1 },
  { id: 'standard', label: 'standard (24)', steps: 28, cfg: 7.0, seeds_per_combo: 2 },
  { id: 'final',    label: 'final (48)',  steps: 36, cfg: 7.5, seeds_per_combo: 4 },
];

function Regen() {
  const models = window.useAsync(() => window.api.models(), []);
  const loras = window.useAsync(() => window.api.loras(), []);
  const projects = window.useAsync(() => window.api.listProjects(), []);
  const toasts = window.useToasts();

  const [project, setProject] = useState('default');
  const [assetKey, setAssetKey] = useState('');
  const [prompt, setPrompt] = useState('');
  const [negative, setNegative] = useState('');
  const [model, setModel] = useState('');
  const [loraSel, setLoraSel] = useState({}); // {name: weight}
  const [preset, setPreset] = useState('standard');
  const [seedsPerCombo, setSeedsPerCombo] = useState(2);
  const [steps, setSteps] = useState(28);
  const [cfg, setCfg] = useState(7.0);
  const [submitting, setSubmitting] = useState(false);
  const [lastBatch, setLastBatch] = useState(null);

  const onPreset = (id) => {
    setPreset(id);
    const p = PRESETS.find((x) => x.id === id);
    if (p) { setSteps(p.steps); setCfg(p.cfg); setSeedsPerCombo(p.seeds_per_combo); }
  };

  const loraGroup = useMemo(() => {
    const arr = Object.entries(loraSel).map(([name, weight]) => ({ name, weight: Number(weight) || 0.7 }));
    return arr.length ? [arr] : [];
  }, [loraSel]);

  const expanded = useMemo(() => {
    const promptCount = prompt.trim() ? 1 : 0;
    const modelCount = model ? 1 : 0;
    const loraCount = loraGroup.length || 1;
    return promptCount * modelCount * loraCount * Math.max(1, seedsPerCombo);
  }, [prompt, model, loraGroup, seedsPerCombo]);

  async function submit(e) {
    e.preventDefault();
    if (submitting) return;
    if (!assetKey.trim()) { toasts.push({ kind: 'error', message: 'asset_key 필요' }); return; }
    if (!prompt.trim())   { toasts.push({ kind: 'error', message: 'prompt 필요' }); return; }
    if (!model)           { toasts.push({ kind: 'error', message: '모델 선택 필요' }); return; }

    const spec = {
      asset_key: assetKey.trim(),
      project,
      category: 'character',
      prompts: [prompt.trim()],
      models: [model],
      loras: loraGroup,
      seeds_per_combo: seedsPerCombo,
      common: {
        steps,
        cfg,
        sampler: 'DPM++ 2M',
        negative_prompt: negative.trim() || null,
        max_colors: 32,
        max_retries: 3,
      },
    };

    setSubmitting(true);
    try {
      const resp = await window.api.createDesignBatch(spec);
      setLastBatch(resp);
      toasts.push({
        kind: 'success',
        message: `batch 생성됨 · ${resp.batch_id} (${resp.expanded_count}장, ETA ${resp.estimated_eta_seconds}s)`,
      });
    } catch (err) {
      toasts.push({ kind: 'error', message: `생성 실패: ${err.message || err}` });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <window.PageToolbar
        left={
          <span className="chip">예상 후보 <b>{expanded || 0}</b>장</span>
        }
        right={
          lastBatch && (
            <window.Link to={`/cherry-pick/${lastBatch.batch_id}`} className="btn primary">
              ▶ 새 배치 열기
            </window.Link>
          )
        }
        info={{
          title: 'regen',
          text: 'POST /api/batches 폼. project × asset_key × prompt × model × lora × seeds_per_combo 로 후보 N장을 즉석 생성. preset 은 steps/cfg/seeds 를 한 번에 바꿔줍니다.',
        }}
      />

      <form onSubmit={submit} className="regen-form" style={{ display: 'grid', gap: 16, gridTemplateColumns: '2fr 1fr' }}>
        <div className="panel-card" style={{ padding: 16 }}>
          <h3>spec</h3>
          <div className="form-grid">
            <label>
              <span>project</span>
              {projects.data && projects.data.length > 0 ? (
                <select className="input" value={project} onChange={(e) => setProject(e.target.value)}>
                  {projects.data.map((p) => (
                    <option key={p.id || p.name || p} value={p.id || p.name || p}>{p.id || p.name || p}</option>
                  ))}
                </select>
              ) : (
                <input className="input" value={project} onChange={(e) => setProject(e.target.value)}/>
              )}
            </label>
            <label>
              <span>asset_key</span>
              <input className="input" value={assetKey} onChange={(e) => setAssetKey(e.target.value)} placeholder="e.g. marine_v2_idle"/>
            </label>
          </div>
          <label className="block">
            <span>prompt</span>
            <textarea className="input" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="positive prompt…"/>
          </label>
          <label className="block">
            <span>negative</span>
            <textarea className="input" rows={2} value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="(optional)"/>
          </label>

          <h3 style={{ marginTop: 18 }}>model</h3>
                 <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
                   <option value="">— choose —</option>
                   {((models.data?.items || models.data?.models || (Array.isArray(models.data) ? models.data : [])) || []).map((m) => {
                     const name = m.name || m;
                     return <option key={name} value={name}>{name}</option>;
                   })}
                 </select>

                 <h3 style={{ marginTop: 18 }}>loras (optional, all combined into one stack)</h3>
                 <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                   {(() => {
                     const items = loras.data?.items || loras.data?.loras
                       || (Array.isArray(loras.data) ? loras.data : []);
                     if (!items || items.length === 0) {
                       return <span style={{ color: 'var(--text-faint)', fontSize: 12 }}>(LoRA 카탈로그 없음)</span>;
                     }
                     return items.map((l) => {
                       const name = l.name || l;
                       const sel = loraSel[name] != null;
                       return (
                         <span
                           key={name}
                           className={`pill ${sel ? 'active' : ''}`}
                           onClick={() => setLoraSel((s) => {
                             const copy = { ...s };
                             if (sel) delete copy[name]; else copy[name] = (l.weight_default ?? 0.7);
                             return copy;
                           })}
                         >{name}{sel && <b style={{ marginLeft: 4 }}>:{loraSel[name]}</b>}</span>
                       );
                     });
                   })()}
                 </div>
        </div>

        <div className="panel-card" style={{ padding: 16, alignSelf: 'start' }}>
          <h3>preset</h3>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {PRESETS.map((p) => (
              <span key={p.id} className={`pill ${preset === p.id ? 'active' : ''}`} onClick={() => onPreset(p.id)}>
                {p.label}
              </span>
            ))}
          </div>
          <div className="form-grid" style={{ marginTop: 14 }}>
            <label><span>steps</span>
              <input className="input" type="number" value={steps} min={1} max={200}
                     onChange={(e) => setSteps(Math.max(1, Math.min(200, parseInt(e.target.value || 0))))}/>
            </label>
            <label><span>cfg</span>
              <input className="input" type="number" step="0.1" value={cfg} min={0} max={30}
                     onChange={(e) => setCfg(parseFloat(e.target.value || 0))}/>
            </label>
            <label><span>seeds/combo</span>
              <input className="input" type="number" value={seedsPerCombo} min={1} max={64}
                     onChange={(e) => setSeedsPerCombo(Math.max(1, Math.min(64, parseInt(e.target.value || 0))))}/>
            </label>
            <label><span>예상 후보 수</span>
              <input className="input" value={expanded || '—'} readOnly style={{ background: 'var(--bg-elev-3)' }}/>
            </label>
          </div>

          <button
            type="submit"
            className="btn primary"
            disabled={submitting}
            style={{ marginTop: 16, width: '100%', padding: '12px', fontSize: 14 }}
          >
            {submitting ? '… 생성 중' : `▶ batch 생성 (${expanded || 0}장)`}
          </button>

          {lastBatch && (
            <div style={{ marginTop: 14, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
              ✓ {lastBatch.batch_id}<br/>
              expanded {lastBatch.expanded_count} · ETA ~{lastBatch.estimated_eta_seconds}s
            </div>
          )}
        </div>
      </form>
    </div>
  );
}

window.Regen = Regen;

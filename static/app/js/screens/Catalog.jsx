/* Catalog — ComfyUI 기반 모델/LoRA/VAE/ControlNet/Workflow 카탈로그.
   PLAN_comfyui_catalog.md §3.3.

   데이터 소스:
     • api.comfyuiCatalog() — 1회 호출. checkpoints / loras / vaes / controlnets / upscalers / workflows 섹션.
     • api.catalogUsage()    — DB 기반 사용 통계 (변경 없음, 호환성).
     • api.catalogUsageBatches({model, lora}) — Detail panel 의 최근 배치 목록.

   섹션 구성: 워크플로우 → Checkpoints → LoRAs → VAEs → ControlNets → Usage(요약).
   빈 카테고리는 카운트 0 으로 노출 (사용자가 "왜 안 뜸" 의심하지 않게).

   ComfyUI down 시 → 에러 배너 + 재시도. PR #45 의 fail-fast (5초) 가 마지노선. */

const { useState, useMemo, useCallback } = React;

function _relTime(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (isNaN(t)) return iso.slice(0, 19).replace('T', ' ');
  const diff = Date.now() - t;
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s 전`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}분 전`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}시간 전`;
  const d = Math.round(h / 24);
  return `${d}일 전`;
}

// catalog kind → catalogUsage map key.
// usage 응답은 {models: {...}, loras: {...}} 만 가짐 — VAE/ControlNet 은 미지원 (DB scope).
const USAGE_KEY = {
  checkpoint: 'models',
  lora: 'loras',
};

// 섹션 정의 — render 순서대로.
const SECTIONS = [
  { key: 'workflows',   kind: 'workflow',    label: '워크플로우',   showFamily: false },
  { key: 'checkpoints', kind: 'checkpoint',  label: 'Checkpoints', showFamily: true },
  { key: 'loras',       kind: 'lora',        label: 'LoRAs',       showFamily: false },
  { key: 'vaes',        kind: 'vae',         label: 'VAEs',        showFamily: false },
  { key: 'controlnets', kind: 'controlnet',  label: 'ControlNets', showFamily: false },
  { key: 'upscalers',   kind: 'upscaler',    label: 'Upscalers',   showFamily: false },
];


function Catalog() {
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState(null); // {kind, item}

  const catalog = window.useAsync(() => window.api.comfyuiCatalog(), []);
  const usage = window.useAsync(
    () => window.api.catalogUsage().catch(() => ({ models: {}, loras: {} })),
    [],
  );

  const data = catalog.data;
  const reload = useCallback(() => { catalog.reload(); usage.reload(); }, [catalog, usage]);

  const sections = useMemo(() => {
    if (!data) return [];
    return SECTIONS.map((sec) => ({
      ...sec,
      items: Array.isArray(data[sec.key]) ? data[sec.key] : [],
    }));
  }, [data]);

  const handleSelect = useCallback((kind, item) => {
    setSelected({ kind, item });
  }, []);

  return (
    <div>
      <window.PageToolbar
        right={
          <>
            {data?.fetched_at && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                fetched {_relTime(data.fetched_at)}
                {data.stale && <span className="pill pill-warn" style={{ marginLeft: 8 }}>stale</span>}
              </span>
            )}
            <button className="btn" onClick={reload} title="새로고침">↻</button>
          </>
        }
        info={{
          title: 'catalog',
          text: 'ComfyUI 기반 워크플로우 / 모델 / LoRA / VAE / ControlNet 카탈로그. 카드 클릭하면 우측에 사용처 + "이 항목으로 batch 만들기" 프리필 링크가 뜹니다. A1111 backend 는 deprecated 되어 데이터 소스에서 빠졌습니다.',
        }}
      />

      <div className="filter-bar" style={{ marginBottom: 12 }}>
        <input
          className="input"
          placeholder="이름 / 카테고리 / family 검색"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 280 }}
        />
        <div style={{ flex: 1 }}/>
        {usage.loading && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>usage 계산 중…</span>}
      </div>

      {catalog.loading && !catalog.data && <window.Skeleton height={180}/>}
      <window.ErrorPanel error={catalog.error} onRetry={reload}/>

      {/* ComfyUI 응답이 ok=false 인 경우도 에러처럼 처리 */}
      {data && data.ok === false && (
        <div className="panel-card" style={{ padding: 14, marginBottom: 14, borderColor: 'var(--accent-fail)' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent-fail)' }}>
            ComfyUI 연결 불가 — catalog 조회 실패
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 4 }}>
            {data.error || 'unknown error'}
          </div>
          <button className="btn" style={{ marginTop: 8 }} onClick={reload}>재시도</button>
        </div>
      )}

      {!catalog.loading && !catalog.error && data && data.ok !== false && (
        <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 380px' : '1fr', gap: 14, alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {sections.map((sec) => (
              <CatalogSection
                key={sec.key}
                section={sec}
                query={q}
                usage={usage.data}
                selected={selected}
                onSelect={handleSelect}
              />
            ))}
          </div>

          {selected && (
            <DetailPanel
              kind={selected.kind}
              item={selected.item}
              onClose={() => setSelected(null)}
            />
          )}
        </div>
      )}
    </div>
  );
}


function CatalogSection({ section, query, usage, selected, onSelect }) {
  const usageMap = (usage && USAGE_KEY[section.kind] && usage[USAGE_KEY[section.kind]]) || {};

  const augmented = useMemo(() => {
    return section.items.map((it) => ({
      ...it,
      _usage: it.name ? (usageMap[it.name] || null) : null,
    }));
  }, [section.items, usageMap]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return augmented;
    return augmented.filter((it) => {
      const haystack = [
        it.name, it.id, it.label, it.category, it.family,
        ...(it.used_by_workflows || []),
      ].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(needle);
    });
  }, [augmented, query]);

  return (
    <section>
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8,
        borderBottom: '1px solid var(--border-soft)', paddingBottom: 4,
      }}>
        <h3 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600 }}>
          {section.label}
        </h3>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
          ({section.items.length})
        </span>
      </div>

      {section.items.length === 0 ? (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)', padding: '4px 0' }}>
          —
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)', padding: '4px 0' }}>
          검색 결과 없음
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 10,
        }}>
          {filtered.map((it) => {
            // workflow row 는 id (e.g. "sprite:pixel_alpha") 만 있고 name 이
            // undefined. model/lora row 는 name 이 있고 id 가 없을 수 있다.
            // 양쪽 모두 안전하게 비교하려면 한 키를 골라 정의된 값일 때만 매칭.
            const itKey = it.id ?? it.name;
            const selKey = selected?.item ? (selected.item.id ?? selected.item.name) : null;
            const isActive = !!(
              selected
              && selected.kind === section.kind
              && itKey != null
              && itKey === selKey
            );
            return (
              <CatalogCard
                key={itKey}
                item={it}
                kind={section.kind}
                showFamily={section.showFamily}
                active={isActive}
                onSelect={() => onSelect(section.kind, it)}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}


function CatalogCard({ item, kind, showFamily, active, onSelect }) {
  const u = item._usage;
  const primaryName = item.name || item.label || item.id || '—';
  const usedBy = Array.isArray(item.used_by_workflows) ? item.used_by_workflows : [];

  return (
    <div
      className={`panel-card catalog-card${active ? ' active' : ''}`}
      onClick={onSelect}
      style={{
        cursor: 'pointer',
        padding: 10,
        borderColor: active ? 'var(--accent-pick)' : undefined,
        outline: active ? '1px solid var(--accent-pick)' : 'none',
        display: 'flex', flexDirection: 'column', gap: 6,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 10,
          color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: 0.5,
        }}>{kind}</span>
        {showFamily && item.family && item.family !== 'unknown' && (
          <span className="pill" style={{ fontSize: 10 }}>{item.family}</span>
        )}
        {kind === 'workflow' && item.variants > 1 && (
          <span className="pill" style={{ fontSize: 10 }}>{item.variants} variants</span>
        )}
        <div style={{ flex: 1 }}/>
        {u && u.batch_count > 0 ? (
          <span className="chip" style={{ color: 'var(--accent-pick)', fontSize: 10 }}>
            {u.batch_count} batches
          </span>
        ) : usedBy.length > 0 ? (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
            used by {usedBy.length} wf
          </span>
        ) : null}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600, wordBreak: 'break-all' }}>
        {primaryName}
      </div>
      {kind === 'workflow' && item.label && item.label !== item.id && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', wordBreak: 'break-word' }}>
          {item.label}
        </div>
      )}
      {(usedBy.length > 0 || u?.last_used_at) && (
        <div style={{ marginTop: 'auto', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
          <span>{u?.task_count ? `${u.task_count} tasks` : (usedBy.length > 0 ? `${usedBy.length} wf` : '—')}</span>
          <span>{u?.last_used_at ? `last ${_relTime(u.last_used_at)}` : ''}</span>
        </div>
      )}
    </div>
  );
}


function DetailPanel({ kind, item, onClose }) {
  const name = item.name || item.id;
  const usageKey = USAGE_KEY[kind]; // 'models' | 'loras' | undefined

  // batch 통계는 model/lora 만 — 그 외 (vae/controlnet/upscaler/workflow) 는 fetch 안 함.
  const params = kind === 'checkpoint' ? { model: name }
    : kind === 'lora' ? { lora: name }
    : null;

  const batches = window.useAsync(
    () => params ? window.api.catalogUsageBatches({ ...params, limit: 20 })
        : Promise.resolve({ items: [] }),
    [kind, name],
  );

  const usedBy = Array.isArray(item.used_by_workflows) ? item.used_by_workflows : [];

  // workflow row 의 경우 — uses_models / uses_loras 가 핵심.
  const workflowUsesModels = Array.isArray(item.uses_models) ? item.uses_models : [];
  const workflowUsesLoras = Array.isArray(item.uses_loras) ? item.uses_loras : [];

  return (
    <aside className="panel-card" style={{
      position: 'sticky', top: 16,
      padding: 14, display: 'flex', flexDirection: 'column', gap: 12,
      maxHeight: 'calc(100vh - 120px)', overflowY: 'auto',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', textTransform: 'uppercase' }}>
            {kind}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600, wordBreak: 'break-all' }}>
            {name}
          </div>
        </div>
        <button className="btn" onClick={onClose} title="닫기">✕</button>
      </div>

      <dl className="meta-block">
        {item.family && <><dt>family</dt><dd>{item.family}</dd></>}
        {item.category && <><dt>category</dt><dd>{item.category}</dd></>}
        {item.label && item.label !== name && <><dt>label</dt><dd>{item.label}</dd></>}
        {kind === 'workflow' && <><dt>variants</dt><dd>{item.variants}</dd></>}
      </dl>

      {usedBy.length > 0 && (
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 4 }}>
            USED BY WORKFLOWS
          </div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {usedBy.map((wf) => (
              <span key={wf} className="pill" style={{ fontSize: 10 }}>{wf}</span>
            ))}
          </div>
        </div>
      )}

      {(workflowUsesModels.length > 0 || workflowUsesLoras.length > 0) && (
        <div>
          {workflowUsesModels.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 4 }}>
                USES MODELS
              </div>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {workflowUsesModels.map((m) => <span key={m} className="pill" style={{ fontSize: 10 }}>{m}</span>)}
              </div>
            </div>
          )}
          {workflowUsesLoras.length > 0 && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 4 }}>
                USES LORAS
              </div>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {workflowUsesLoras.map((l) => <span key={l} className="pill" style={{ fontSize: 10 }}>{l}</span>)}
              </div>
            </div>
          )}
        </div>
      )}

      {usageKey && (
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 6 }}>최근 BATCHES</div>
          {batches.loading && <window.Skeleton height={40}/>}
          {batches.error && <window.ErrorPanel error={batches.error} onRetry={batches.reload}/>}
          {batches.data && (batches.data.items || []).length === 0 && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
              아직 사용된 배치가 없습니다.
            </div>
          )}
          {batches.data && (batches.data.items || []).length > 0 && (
            <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {batches.data.items.map((b) => (
                <li key={b.batch_id}>
                  <a
                    href={`/app/batches/${b.batch_id}`}
                    onClick={(e) => { e.preventDefault(); window.navigate(`/batches/${b.batch_id}`); }}
                    style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '6px 8px', borderRadius: 4,
                      background: 'var(--bg-elev-2)',
                      fontFamily: 'var(--font-mono)', fontSize: 11,
                      textDecoration: 'none', color: 'var(--text-primary)',
                    }}
                    title={b.batch_id}
                  >
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {b.asset_key}
                    </span>
                    <span style={{ color: 'var(--text-faint)', marginLeft: 8 }}>
                      {_relTime(b.last_updated_at)}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {(kind === 'checkpoint' || kind === 'lora') && (
        <a
          className="btn btn-primary"
          href={`/app/batches/new?${kind === 'checkpoint' ? 'model' : 'lora'}=${encodeURIComponent(name || '')}`}
          onClick={(e) => {
            e.preventDefault();
            const param = kind === 'checkpoint' ? 'model' : 'lora';
            window.navigate(`/batches/new?${param}=${encodeURIComponent(name || '')}`);
          }}
          style={{ textAlign: 'center', textDecoration: 'none' }}
        >
          ▶ 이 {kind} 로 batch 만들기
        </a>
      )}
      <button
        className="btn"
        onClick={() => {
          try { navigator.clipboard.writeText(name || ''); } catch (_) { /* noop */ }
        }}
      >
        이름 복사
      </button>
    </aside>
  );
}

window.Catalog = Catalog;

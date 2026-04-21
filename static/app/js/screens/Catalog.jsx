/* Catalog — SD models + LoRAs, card view with usage backref.
   Spec §6.8:
     • 카드 UI (이름 · 태그 · overlay 메타)
     • "사용된 batch 수" · "마지막 사용 시각" (usage 엔드포인트)
     • 클릭 → 우측 상세 패널 (최근 batch 리스트 + "이 모델로 batch 만들기" 프리필)
   Metadata comes from A1111 merged with config/sd_catalog.yml (읽기 전용). */

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

function Catalog() {
  const [tab, setTab] = useState('models');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState(null); // {kind:'model'|'lora', name}

  const models = window.useAsync(() => window.api.models(), []);
  const loras = window.useAsync(() => window.api.loras(), []);
  const usage = window.useAsync(() => window.api.catalogUsage().catch(() => ({ models: {}, loras: {} })), []);

  const source = tab === 'models' ? models : loras;
  const items = source.data?.items || [];
  const usageMap = (tab === 'models' ? usage.data?.models : usage.data?.loras) || {};

  const augmented = useMemo(() => items.map((it) => ({
    ...it,
    _usage: usageMap[it.name] || null,
  })), [items, usageMap]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const base = !needle ? augmented : augmented.filter((it) => {
      const haystack = [it.name, it.title, it.alias, it.filename, (it.tags || []).join(' '), it.notes]
        .filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(needle);
    });
    return [...base].sort((a, b) => {
      const ua = a._usage?.last_used_at || '';
      const ub = b._usage?.last_used_at || '';
      if (ua && ub) return ua < ub ? 1 : -1;
      if (ua) return -1;
      if (ub) return 1;
      return String(a.name || '').localeCompare(String(b.name || ''));
    });
  }, [augmented, q]);

  const handleSelect = useCallback((it) => {
    setSelected({ kind: tab === 'models' ? 'model' : 'lora', item: it });
  }, [tab]);

  return (
    <div>
      <window.PageToolbar
        right={
          <>
            {source.data?.catalog_path && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                overlay: {source.data.catalog_path}
                {!source.data.catalog_present && <span className="pill pill-fail" style={{ marginLeft: 8 }}>missing</span>}
              </span>
            )}
            <button className="btn" onClick={() => { models.reload(); loras.reload(); usage.reload(); }} title="새로고침">↻</button>
          </>
        }
        info={{
          title: 'catalog',
          text: 'A1111 SD 서버 models/loras + config/sd_catalog.yml overlay. 카드를 클릭하면 우측에 최근 사용 batch 와 "이 모델/LoRA 로 batch 만들기" 프리필 링크가 뜹니다. YAML 편집은 인프라 레포에서.',
        }}
      />

      <div className="tabs">
        <button className={`tab ${tab === 'models' ? 'active' : ''}`} onClick={() => { setTab('models'); setSelected(null); }}>
          /models <span style={{ color: 'var(--text-faint)' }}>({models.data?.count ?? '—'})</span>
        </button>
        <button className={`tab ${tab === 'loras' ? 'active' : ''}`} onClick={() => { setTab('loras'); setSelected(null); }}>
          /loras <span style={{ color: 'var(--text-faint)' }}>({loras.data?.count ?? '—'})</span>
        </button>
      </div>

      <div className="filter-bar" style={{ marginBottom: 12 }}>
        <input
          className="input"
          placeholder="이름 / 태그 / 파일명 검색"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 280 }}
        />
        <div style={{ flex: 1 }}/>
        {usage.loading && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>usage 계산 중…</span>}
      </div>

      {source.loading && !source.data && <window.Skeleton height={180}/>}
      <window.ErrorPanel error={source.error} onRetry={source.reload}/>

      {!source.loading && !source.error && (
        <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 380px' : '1fr', gap: 14, alignItems: 'flex-start' }}>
          <div>
            {filtered.length === 0 ? (
              <window.EmptyState title="검색 결과 없음" hint={q ? `'${q}' 와(과) 일치하는 항목이 없습니다.` : '카탈로그가 비어 있습니다.'}/>
            ) : (
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
                gap: 12,
              }}>
                {filtered.map((it) => (
                  <CatalogCard
                    key={it.name || it.filename || it.alias}
                    item={it}
                    kind={tab === 'models' ? 'model' : 'lora'}
                    active={selected?.item?.name === it.name}
                    onSelect={() => handleSelect(it)}
                  />
                ))}
              </div>
            )}
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

function CatalogCard({ item, kind, active, onSelect }) {
  const u = item._usage;
  const primaryName = item.name || item.title || item.alias || '—';
  const subtitle = kind === 'model'
    ? (item.title && item.title !== primaryName ? item.title : item.filename)
    : (item.alias && item.alias !== primaryName ? item.alias : (item.weight_range ? `weight ${item.weight_range[0]}–${item.weight_range[1]}` : null));

  return (
    <div
      className={`panel-card catalog-card${active ? ' active' : ''}`}
      onClick={onSelect}
      style={{
        cursor: 'pointer',
        padding: 12,
        borderColor: active ? 'var(--accent-pick)' : undefined,
        outline: active ? '1px solid var(--accent-pick)' : 'none',
        display: 'flex', flexDirection: 'column', gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 10,
          color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: 0.5,
        }}>{kind}</span>
        {item.has_metadata && <span className="pill pill-ok" style={{ fontSize: 10 }}>yaml</span>}
        <div style={{ flex: 1 }}/>
        {u && u.batch_count > 0 ? (
          <span className="chip" style={{ color: 'var(--accent-pick)', fontSize: 10 }}>
            {u.batch_count} batches
          </span>
        ) : (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>미사용</span>
        )}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600, wordBreak: 'break-all' }}>
        {primaryName}
      </div>
      {subtitle && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', wordBreak: 'break-all' }}>
          {subtitle}
        </div>
      )}
      {item.tags && item.tags.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {item.tags.slice(0, 6).map((t) => (
            <span key={t} className="pill" style={{ fontSize: 10 }}>{t}</span>
          ))}
          {item.tags.length > 6 && (
            <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>+{item.tags.length - 6}</span>
          )}
        </div>
      )}
      <div style={{ marginTop: 'auto', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
        <span>{u?.task_count ? `${u.task_count} tasks` : 'tasks —'}</span>
        <span>{u?.last_used_at ? `last ${_relTime(u.last_used_at)}` : 'last —'}</span>
      </div>
    </div>
  );
}

function DetailPanel({ kind, item, onClose }) {
  const name = item.name;
  const params = kind === 'model' ? { model: name } : { lora: name };
  const batches = window.useAsync(
    () => window.api.catalogUsageBatches({ ...params, limit: 20 }),
    [kind, name],
  );

  const prefillHref = `/app/batches/new?${kind}=${encodeURIComponent(name || '')}`;
  const description = item.description || item.desc || item.notes || null;
  const samplePrompt = item.sample_prompt || item.example_prompt || null;
  const compatibleModels = item.compatible_models || item.compatible || item.compat_models || [];

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
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 600, wordBreak: 'break-all' }}>
            {name}
          </div>
        </div>
        <button className="btn" onClick={onClose} title="닫기">✕</button>
      </div>

      <dl className="meta-block">
        {item.title && item.title !== name && (<><dt>title</dt><dd>{item.title}</dd></>)}
        {item.alias && item.alias !== name && (<><dt>alias</dt><dd>{item.alias}</dd></>)}
        {item.filename && (<><dt>filename</dt><dd style={{ wordBreak: 'break-all' }}>{item.filename}</dd></>)}
        {item.hash && (<><dt>hash</dt><dd style={{ fontFamily: 'var(--font-mono)' }}>{String(item.hash).slice(0, 16)}</dd></>)}
        {item.weight_range && (<><dt>weight range</dt><dd>{item.weight_range[0]}–{item.weight_range[1]}</dd></>)}
        {item.weight_default != null && (<><dt>default weight</dt><dd>{item.weight_default}</dd></>)}
        {description && (<><dt>description</dt><dd>{description}</dd></>)}
      </dl>

      {samplePrompt && (
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 4 }}>SAMPLE PROMPT</div>
          <pre style={{ margin: 0, padding: 8, background: 'var(--bg-elev-2)', borderRadius: 4, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {samplePrompt}
          </pre>
        </div>
      )}

      {Array.isArray(compatibleModels) && compatibleModels.length > 0 && (
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 4 }}>COMPATIBLE MODELS</div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {compatibleModels.map((m) => <span key={m} className="pill" style={{ fontSize: 10 }}>{m}</span>)}
          </div>
        </div>
      )}

      {item.tags && item.tags.length > 0 && (
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 4 }}>TAGS</div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {item.tags.map((t) => <span key={t} className="pill" style={{ fontSize: 10 }}>{t}</span>)}
          </div>
        </div>
      )}

      <div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)', marginBottom: 6 }}>최근 BATCHES</div>
        {batches.loading && <window.Skeleton height={40}/>}
        {batches.error && <window.ErrorPanel error={batches.error} onRetry={batches.reload}/>}
        {batches.data && batches.data.items.length === 0 && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
            아직 사용된 배치가 없습니다.
          </div>
        )}
        {batches.data && batches.data.items.length > 0 && (
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

      <a
        className="btn btn-primary"
        href={prefillHref}
        onClick={(e) => {
          e.preventDefault();
          window.navigate(`/batches/new?${kind}=${encodeURIComponent(name || '')}`);
        }}
        style={{ textAlign: 'center', textDecoration: 'none' }}
      >
        ▶ 이 {kind} 로 batch 만들기
      </a>
      <button
        className="btn"
        onClick={() => {
          try {
            navigator.clipboard.writeText(name || '');
          } catch (_) { /* noop */ }
        }}
      >
        이름 복사
      </button>
    </aside>
  );
}

window.Catalog = Catalog;

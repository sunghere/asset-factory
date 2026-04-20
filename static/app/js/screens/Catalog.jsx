/* Catalog — SD models + LoRAs. Metadata comes from A1111 merged with
   config/sd_catalog.yml (see server.py::sd_catalog_*).  Read-only; we do
   NOT edit the YAML from here to keep infra config as code. */

const { useState, useMemo } = React;

function Catalog() {
  const [tab, setTab] = useState('models');
  const [q, setQ] = useState('');

  const models = window.useAsync(() => window.api.models(), []);
  const loras = window.useAsync(() => window.api.loras(), []);

  const data = tab === 'models' ? models : loras;
  const items = data.data?.items || [];

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((it) => {
      const haystack = [it.name, it.title, it.alias, it.filename, (it.tags || []).join(' '), it.notes]
        .filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(needle);
    });
  }, [items, q]);

  return (
    <div>
      <window.PageToolbar
        right={
          <>
            {data.data?.catalog_path && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                overlay: {data.data.catalog_path}
                {!data.data.catalog_present && <span className="pill pill-fail" style={{ marginLeft: 8 }}>missing</span>}
              </span>
            )}
            <button className="btn" onClick={() => { models.reload(); loras.reload(); }} title="새로고침">↻</button>
          </>
        }
        info={{
          title: 'catalog',
          text: 'A1111 SD 서버의 models/loras 목록에 config/sd_catalog.yml overlay 를 합쳐 보여줍니다. 읽기 전용 — YAML 편집은 인프라 레포에서.',
        }}
      />

      <div className="tabs">
        <button className={`tab ${tab === 'models' ? 'active' : ''}`} onClick={() => setTab('models')}>
          /models <span style={{ color: 'var(--text-faint)' }}>({models.data?.count ?? '—'})</span>
        </button>
        <button className={`tab ${tab === 'loras' ? 'active' : ''}`} onClick={() => setTab('loras')}>
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
        {data.data?.catalog_path && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)' }}>
            overlay: {data.data.catalog_path}
            {!data.data.catalog_present && <span className="pill pill-fail" style={{ marginLeft: 8 }}>missing</span>}
          </span>
        )}
      </div>

      {data.loading && <window.Skeleton height={180}/>}
      <window.ErrorPanel error={data.error} onRetry={data.reload}/>

      {!data.loading && !data.error && (
        filtered.length === 0 ? (
          <window.EmptyState title="검색 결과 없음" hint={q ? `'${q}' 와(과) 일치하는 항목이 없습니다.` : '카탈로그가 비어 있습니다.'}/>
        ) : tab === 'models' ? <ModelsTable rows={filtered}/> : <LorasTable rows={filtered}/>
      )}
    </div>
  );
}

function TagList({ tags }) {
  if (!tags || tags.length === 0) return <span style={{ color: 'var(--text-faint)' }}>—</span>;
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {tags.map((t) => <span key={t} className="pill" style={{ fontSize: 10 }}>{t}</span>)}
    </div>
  );
}

function ModelsTable({ rows }) {
  return (
    <div className="panel-card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>name</th>
            <th>title</th>
            <th>filename</th>
            <th>tags</th>
            <th>hash</th>
            <th>meta</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.name || m.title}>
              <td style={{ fontWeight: 600 }}>{m.name || '—'}</td>
              <td style={{ color: 'var(--text-muted)' }}>{m.title || '—'}</td>
              <td style={{ color: 'var(--text-faint)', fontSize: 11 }}>{m.filename || '—'}</td>
              <td><TagList tags={m.tags}/></td>
              <td style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-faint)' }}>
                {m.hash ? String(m.hash).slice(0, 10) : '—'}
              </td>
              <td>
                {m.has_metadata
                  ? <span className="pill pill-ok">yaml</span>
                  : <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LorasTable({ rows }) {
  return (
    <div className="panel-card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>name</th>
            <th>alias</th>
            <th>weight range</th>
            <th>tags</th>
            <th>notes</th>
            <th>meta</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((l) => (
            <tr key={l.name || l.alias}>
              <td style={{ fontWeight: 600 }}>{l.name || '—'}</td>
              <td style={{ color: 'var(--text-muted)' }}>{l.alias || '—'}</td>
              <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                {l.weight_range ? `${l.weight_range[0]}–${l.weight_range[1]}` : '—'}
              </td>
              <td><TagList tags={l.tags}/></td>
              <td style={{ color: 'var(--text-muted)', fontSize: 11 }}>{l.notes || '—'}</td>
              <td>
                {l.has_metadata
                  ? <span className="pill pill-ok">yaml</span>
                  : <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

window.Catalog = Catalog;

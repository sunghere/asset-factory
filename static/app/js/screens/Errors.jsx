/* Errors — developer-facing showcase of error/empty states.

   The app's global ErrorBoundary lives in app.jsx. This screen lets us
   eyeball the visual treatment of common failure surfaces. */

function Errors() {
  const toasts = window.useToasts();
  return (
    <div>
      <window.PageToolbar
        left={<span className="chip">internal · 에러/빈 상태 미리보기</span>}
        info={{ title: 'errors · preview', text: '개발용 미리보기 — 에러 배너/토스트/빈 상태/스켈레톤/ErrorBoundary 테스트 버튼.' }}
      />

      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(2, 1fr)' }}>
        <section className="panel-card" style={{ padding: 16 }}>
          <h3>error banner</h3>
          <div className="error-banner">
            <span>⚠</span>
            <span>SD 서버 응답 없음 (5xx) — 마지막 성공 12:14:30 KST</span>
            <button className="action" style={{ marginLeft: 'auto' }}>재시도</button>
          </div>
        </section>

        <section className="panel-card" style={{ padding: 16 }}>
          <h3>toast (success + undo)</h3>
          <button className="btn" onClick={() => toasts.push({
            kind: 'success',
            message: '채택됨 · marine_v2_idle',
            onUndo: () => toasts.push({ kind: 'info', message: '실행취소됨' }),
          })}>토스트 띄우기</button>
        </section>

        <section className="panel-card" style={{ padding: 16 }}>
          <h3>empty state</h3>
          <window.EmptyState glyph="∅" title="후보 없음" hint="이 batch 에는 후보가 없습니다."/>
        </section>

        <section className="panel-card" style={{ padding: 16 }}>
          <h3>skeleton</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <window.Skeleton/>
            <window.Skeleton width="60%"/>
            <window.Skeleton height={80}/>
          </div>
        </section>

        <section className="panel-card" style={{ padding: 16 }}>
          <h3>throw runtime error (전역 ErrorBoundary 테스트)</h3>
          <BoomButton/>
        </section>
      </div>
    </div>
  );
}

function BoomButton() {
  const [boom, setBoom] = React.useState(false);
  if (boom) throw new Error('의도적 에러 — ErrorBoundary 가 잡아야 함');
  return <button className="btn" onClick={() => setBoom(true)}>throw</button>;
}

window.Errors = Errors;

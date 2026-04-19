# Asset Factory — 설계 리뷰 & 보완사항

> **리뷰어:** Claude (2025-01-20)
> **대상:** DESIGN.md + HANDOFF.md 기반 검토

---

## 1. 동시성 / 큐 설계

SD 서버가 1대(RTX 4080 SUPER)이므로 배치 119개를 동시에 보내면 안 됨.

### 권장 구현
- **asyncio.Queue** 기반 워커 패턴
- 동시 생성 수: **1~2개** (VRAM 16GB 기준)
- 실패 시 재시도: **최대 3회**, exponential backoff (2초 → 4초 → 8초)
- 큐 상태를 DB에 영속화 → 서버 재시작 시 이어서 처리

```python
# 예시 구조
class GenerationQueue:
    def __init__(self, max_concurrent=1):
        self.queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def worker(self):
        while True:
            job = await self.queue.get()
            async with self.semaphore:
                await self.process_job(job)
```

---

## 2. 실시간 진행률 (WebSocket/SSE)

polling도 되지만, UI 경험을 위해 **SSE(Server-Sent Events)** 추천:
- WebSocket보다 구현 단순
- FastAPI에서 `StreamingResponse`로 바로 지원
- 에셋 생성 완료, 검증 결과 등 이벤트 push

```python
@app.get("/api/events")
async def events():
    async def event_stream():
        while True:
            event = await event_queue.get()
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

---

## 3. 최소 인증

LAN 바인드(`0.0.0.0:3200`)이므로 최소한의 보안 필요:
- `.env`에 `API_KEY` 설정
- 생성/삭제/승인 등 변경 API에만 헤더 체크
- UI 접속은 자유 (읽기 전용)

---

## 4. 이미지 정리 정책 (GC)

후보 N장 중 승인되지 않은 이미지 관리:
- 리젝 후 **7일** 보관 → 자동 삭제
- 또는 디스크 사용량 **1GB 초과** 시 오래된 것부터 정리
- `data/candidates/` 디렉토리에 후보 저장, `data/approved/`에 승인본 복사

---

## 5. 에러 핸들링 상세

| 상황 | 처리 |
|------|------|
| SD 서버 unreachable | 3회 재시도 (backoff) → 실패 시 job 상태 `error` + UI 알림 |
| SD 서버 OOM | 요청 크기 줄여서 재시도 (해상도 단계 하향) |
| Aseprite CLI 실패 | 로그 저장 + job 상태 `error` |
| 디스크 풀 | 생성 전 여유공간 체크, 부족 시 거부 |

---

## 6. Cursor 개발 가이드

### 프로젝트 구조
```
asset-factory/
├── .venv/                 # Python 3.11 가상환경
├── .env                   # 환경변수 (SD_HOST, ASEPRITE_PATH 등)
├── requirements.txt       # 의존성
├── server.py              # FastAPI 메인 (여기서 시작)
├── models.py              # SQLite 모델
├── generator.py           # SD API 호출
├── validator.py           # 에셋 검증
├── scanner.py             # 디렉토리 스캔
├── specs/                 # 에셋 스펙 JSON
├── static/                # 프론트엔드 (SPA)
│   ├── index.html
│   ├── app.js
│   └── style.css
├── data/                  # SQLite DB + 생성 이미지
├── DESIGN.md              # 전체 설계
└── HANDOFF.md             # 인프라/제약사항
```

### 실행 방법
```bash
cd ~/workspace/asset-factory
source .venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 3200 --reload
```

### 구현 순서 (Phase 1 → MVP)
1. `models.py` — SQLite 스키마 (projects, assets, jobs 테이블)
2. `server.py` — FastAPI 앱 기본 + 헬스체크 API
3. `generator.py` — SD API txt2img 호출
4. `validator.py` — 이미지 메타데이터 검증 (크기, 색상수, 투명도)
5. 통합 테스트: 단일 에셋 생성 → 검증 → DB 저장

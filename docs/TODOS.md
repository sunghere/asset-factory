# TODOS — 후속 작업 추적

> 각 항목은 **컨텍스트가 휘발되기 전에** 별도 PR/세션으로 처리. 1차 본 PR 범위 밖이지만
> 잊으면 안 되는 것들을 모은다. 항목은 우선순위 (P0 > P1 > P2) + 발생 컨텍스트 라벨로 분류.
>
> 라벨:
> - `comfyui-inputs` — [PLAN_comfyui_dynamic_inputs.md](./PLAN_comfyui_dynamic_inputs.md) 1차 PR 후속
> - `cli` — `af.mjs` 등 CLI 인프라
> - `ops` — GC/cron/모니터링
> - `ui` — 화면 노출 (SCREEN_SPEC v0.2 후속)
> - `docs` — 문서 정합성
> - `windows` — Windows 환경 관련 회귀 방지
>
> 형식: `- [ ] [라벨] [P?] 한 줄 제목 — 세부 (file:line 또는 명세 위치)`

---

## P0 — 운영 지장 / 보안

- [ ] `[comfyui-inputs] [P0]` **ComfyUI input/ GC cron** — 사용자가 수동 정리 못 따라잡으면
      디스크 폭주. 설계: `scripts/comfyui_input_gc.py` 신설, `task.workflow_params_json` 의
      JSON 파싱으로 N일 (기본 30일) 미참조 파일 삭제. 참조 추적 흐름:
      [models.py:639](../models.py:639) `get_asset()` 와 동급의 `list_recent_input_refs()` 추가
      → cron 에서 호출. 운영 호스트 ([HANDOFF.md](../HANDOFF.md)) 의 Windows Task Scheduler 등록.
      **컨텍스트**: 1차 PR 의 §5.2 deferred.

- [ ] `[docs] [P0]` **`workflows/README.md:128` 의 `af workflow gen` 광고 정리** — `af.mjs` 가
      레포에 부재한데 README 가 광고하면 신규 컨트리뷰터 막힘. CLI 신설 PR 전에는 README 에
      "CLI 미구현, 직접 `curl /api/workflows/generate` 호출 사용" 명시 + curl 예시 링크.
      **컨텍스트**: PLAN 단계 6 의 `docs/COMFYUI_DYNAMIC_INPUTS.md` 와 cross-link.

---

## P1 — 사용자 경험 / 개발 효율

- [ ] `[cli] [P1]` **`af.mjs` CLI 신설** — `af workflow upload <local.png>`, `af workflow upload
      --from-asset <id>`, `af workflow gen <variant> ... --upload local:key` 묶음 옵션.
      추정 ~2h. PLAN 1차 PR 의 단계 6 curl 예시를 그대로 wrapper 로 감싸면 됨.
      Node.js 런타임 필요 (`.mjs`) — 또는 Python 으로 갈지 (이름 통일 위해 `af.py` + entry script)
      결정 필요. **선결**: 위 P0 docs 정리 의존 (광고 일관성).

- [ ] `[comfyui-inputs] [P1]` **URL fetch 입력 경로** (`POST /api/workflows/inputs/from-url`) —
      Slack/Drive 외부 URL → input/. SSRF 방어 (`localhost`/`169.254.x.x`/`10.x` 거부)
      필수. content-length 사전 HEAD 검증. PIL verify 동일.
      **컨텍스트**: PLAN §3 (c) deferred.

- [ ] `[comfyui-inputs] [P1]` **task → task chain 자동화** — 현재는 PoseExtract 결과를 다음
      변형 입력으로 쓰려면 사용자가 2-step (upload --from-asset + workflow_params 명시).
      DB 마이그레이션 필요: `tasks` 테이블에 `parent_task_id`, `chain_role` 컬럼 추가.
      [server.py:607](../server.py:607) `claim_next_task()` 에 chain 분기.
      **확장 검토**: 단순 chain 보다 `task.spec.chain: [{variant, params_from: prev.output}]`
      DSL 도입이 나을지.

- [ ] `[ui] [P1]` **PoseExtract 전용 화면** — [docs/SCREEN_SPEC_v0.2.md](./SCREEN_SPEC_v0.2.md)
      에 11화면 정의돼 있는데 PoseExtract 흐름은 없음. 사용자 이미지 drop → preview →
      "Extract pose" 버튼 → 결과 grid 미리보기 → "Use as ControlNet input for next gen"
      바로가기 흐름. 1차 PR 의 HTTP API 가 그대로 재사용 가능.

- [ ] `[comfyui-inputs] [P1]` **multipart streaming 전환** (현 메모리 로드) — 100MB+ 입력
      시 메모리 부담. fastapi `UploadFile` 의 `await file.read()` 대신 chunked → aiohttp
      `FormData` 도 stream 지원 검증. 단 PIL verify 는 결국 메모리 로드 필요 → 실효성 검토.

---

## P2 — 향후 확장 / 정리

- [ ] `[comfyui-inputs] [P2]` **A1111 백엔드 호환 검토** — 현재 `upload_input_image` 는
      ComfyUI 전용. A1111 에 동등 API 가 있는지 (`/sdapi/v1/upload` 등), 또는 `sd_backend.py`
      추상화 레이어에 입력 업로드 메서드를 노출할지.

- [ ] `[ops] [P2]` **업로드 메트릭** — `task.workflow_params_json` 안 input 파일 통계
      (총 업로드 수, 평균 크기, 캐시 hit 률 = sha 중복) 를 [server.py](../server.py) 의
      `/api/system/stats` 같은 엔드포인트에 노출.

- [ ] `[windows] [P2]` **Windows venv CI 매트릭스** — 현재 GitHub Actions 가 어느 OS 에서
      도는지 [.github/workflows/](../.github/workflows/) 확인 후, Windows runner 추가 검토.
      이번 세션에서 발견한 [server.py:71](../server.py:71) `os.pathsep` /
      [scanner.py:12](../scanner.py:12) `as_posix()` 같은 회귀를 자동 잡기 위함.
      **선결**: pytest config 정합성 (아래 항목).

- [ ] `[docs] [P2]` **pytest config 일원화** — 현재 [pytest.ini](../pytest.ini) 와
      [pyproject.toml:25-28](../pyproject.toml:25) 의 `[tool.pytest.ini_options]` 가 둘 다
      있어서 후자 무시됨 (실행 시 경고). `pyproject.toml` 한 곳으로 통일 + `pytest.ini` 삭제.
      `addopts="-q"`, `pythonpath="."` 가 살아남.

- [ ] `[comfyui-inputs] [P2]` **`_LOAD_IMAGE_RULES` 라벨 → workflow JSON 의 LoadImage
      title 자동 추출** — 현재는 워크플로우마다 사람이 보고 정규식 등록. 향후 `workflows/`
      안의 모든 JSON 을 한 번 스캔해 `class_type=LoadImage` 의 title 목록을 뽑아
      `_LOAD_IMAGE_RULES` 와 diff → 누락된 라벨 경고. 라벨 일관성 도구.

- [ ] `[ops] [P2]` **`af workflow upload` 의 동시성 race 고려** — sha 기반 명명이라 같은
      bytes 면 무해하지만, 다른 워커가 동시에 다른 bytes 같은 이름 (sha 충돌 시) 올리면 last
      write wins. `overwrite=False` + `(1).png` suffix 폴백 검토. (현 PLAN 은 overwrite=True.)

---

## 완료/이관

> 항목 끝나면 PR 링크와 함께 여기 옮기고, 일정 후 정리.

- (없음)

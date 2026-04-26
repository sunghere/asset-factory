# HANDOFF — ComfyUI 동적 입력 1차 PR (작업 B)

> **상태:** 미착수, 신규 세션이 픽업
> **작성:** 2026-04-26 · 이전 세션 종료 직후
> **상위 설계서:** [`docs/PLAN_comfyui_dynamic_inputs.md`](./PLAN_comfyui_dynamic_inputs.md)
> **백로그/후속:** [`docs/TODOS.md`](./TODOS.md)

---

## 0. TL;DR

ComfyUI 의 `POST /upload/image` 통과로 임의 이미지를 input/ 에 올려 LoadImage
노드가 참조 가능하게 만든다 (PoseExtract 의 source image 등). 1차 PR 은 8단계
(~3.5h) — multipart 직업로드 + 기존 asset 참조 + `workflow_patcher` generic
`load_images` 디스패치 + PIL 디코딩 검증 + 통합 smoke 문서화. CLI 는 1차 제외.

**선결 (사용자 외부 작업):** §3 의 단계 0b (curl 1회 — ComfyUI subfolder
mkdir 동작 실측). 결과에 따라 fallback 분기 결정.

---

## 1. 직전 세션 (2026-04-26) 에 끝난 것

**PR #13 머지 완료** ([4de9fbf](https://github.com/sunghere/asset-factory/commit/4de9fbf), squash):
- ComfyUI 백엔드 스택: `generator_comfyui.py`, `sd_backend.py`,
  `workflow_registry.py`, `workflow_patcher.py`
- 워크플로우 카탈로그: `workflows/registry.yml` + 41개 API JSON
- 신규 엔드포인트: `GET /api/workflows/catalog`, `POST /api/workflows/generate`
- 테스트: `tests/test_{sd_backend,workflow_endpoints,workflow_patcher,workflow_registry,generator_comfyui}.py`
- Windows 회귀 fix: `server.py:_allowed_roots` (os.pathsep), `scanner.py:infer_category` (as_posix)
- 1차 리뷰 + 비판 검토 후 P1×9 + P2×5 모두 같은 PR 에 반영
- 검증: **202 passed**, ruff clean

### 본 작업이 의존하는 PR #13 산출물

| 컴포넌트 | 파일:라인 | 역할 |
|---|---|---|
| `ComfyUIClient` | [generator_comfyui.py:88](../generator_comfyui.py:88) | HTTP 클라이언트 — 새 `upload_input_image()` 메서드 추가될 곳. lazy session + `__aenter__/__aexit__` 이미 갖춤 |
| `_classify_comfy_failure` | [generator_comfyui.py:70](../generator_comfyui.py:70) | 에러 코드 매핑. 새 메서드도 그대로 재사용 |
| `_get_session()` / `close()` | [generator_comfyui.py:135-153](../generator_comfyui.py:135) | 세션 재사용 인프라. 새 메서드도 같은 패턴 |
| `SDError` | [generator.py:15](../generator.py:15) | 에러 모델 |
| `_safe_segment` | [server.py:125](../server.py:125) | 경로 sanitizer (이미 존재). 본 작업의 `_safe_subfolder` 와 의미 다름 — 별도 추가 필요 |
| `_ensure_path_allowed` | [server.py:103](../server.py:103) | path traversal 방어. `from-asset` 엔드포인트가 이용 |
| `comfyui_client` 전역 | [server.py:320](../server.py:320) | 모듈 스코프. 새 엔드포인트가 import 해 호출 |
| `require_api_key` | [server.py:591](../server.py:591) | 인증 디코레이터. 새 엔드포인트 모두 적용 |
| `Database.get_asset(asset_id)` | [models.py:639](../models.py:639) | `from-asset` 엔드포인트가 호출 |
| `patch_workflow()` | [workflow_patcher.py:177](../workflow_patcher.py:177) | 본 작업이 `load_images: dict` kwarg 추가할 곳. 이미 `ksampler_overrides` 가 들어가 있어 패턴 참고 가능 |
| `_RULES` | [workflow_patcher.py:97](../workflow_patcher.py:97) | LoadImage 규칙 (`pose_image` 만) — 본 작업이 `_LOAD_IMAGE_RULES` 로 분리할 후보 |
| `find_nodes()` | [workflow_patcher.py:44](../workflow_patcher.py:44) | class_type + title 정규식 매칭 헬퍼. 그대로 사용 |

---

## 2. PLAN 의 8단계 — 그대로 시작 가능

[`docs/PLAN_comfyui_dynamic_inputs.md`](./PLAN_comfyui_dynamic_inputs.md) §4 의 단계
0~7 (총 ~3.5h). 핵심 변경:

| # | 단계 | 추정 | 어디 수정 |
|---|---|---|---|
| 0 | PoseExtract title grep — **완료 ✓** (`"Load source image"` exact match) | done | — |
| **0b** | ComfyUI subfolder mkdir 실측 (사용자 curl 1회) | 5min | 사용자 외부 작업 |
| 1 | `_safe_subfolder()` + `_safe_input_filename()` 헬퍼 + 테스트 | 20min | `server.py` (after [server.py:125](../server.py:125) `_safe_segment`), `tests/test_safe_path_helpers.py` 신규 |
| 2 | `ComfyUIClient.upload_input_image()` + 단위 테스트 | 35min | [generator_comfyui.py](../generator_comfyui.py) — `submit_and_wait` 직전쯤. 테스트는 [tests/test_generator_comfyui.py](../tests/test_generator_comfyui.py) 보강 (aioresponses 이미 사용 중) |
| 3 | `POST /api/workflows/inputs` (multipart + PIL verify) | 35min | `server.py` — `/api/workflows/generate` 직전. `tests/test_workflow_inputs_endpoint.py` 신규 |
| 4 | `POST /api/workflows/inputs/from-asset` | 25min | 위 같은 위치 |
| 5 | `workflow_patcher` `load_images: dict` generic + `_LOAD_IMAGE_RULES` + 테스트 | 40min | [workflow_patcher.py](../workflow_patcher.py), [tests/test_workflow_patcher.py](../tests/test_workflow_patcher.py) 보강 |
| 6 | curl 2-step 시나리오 + 문서화 | 20min | `docs/COMFYUI_DYNAMIC_INPUTS.md` 신규 또는 `workflows/README.md` 보강 |
| 7 | 통합 smoke (사용자 — 실 ComfyUI 로 PoseExtract end-to-end) | 30min | 사용자 외부 |

---

## 3. 단계 0b 상세 — 시작 전 사용자가 해야 할 것

ComfyUI 의 `POST /upload/image` 가 존재하지 않는 subfolder 를 **자동 생성하는지**
실측. 결과에 따라 단계 1 의 `_safe_subfolder` 디폴트와 단계 2 의 호출 패턴이 달라짐.

```bash
# 사용자 PC 에서 (subfolder 가 없는 새 이름으로 시도):
curl -F image=@some_test.png \
     -F subfolder=test-mkdir-$(date +%s) \
     -F type=input \
     -F overwrite=true \
     http://192.168.50.225:8188/upload/image

# Windows ComfyUI 호스트에서 (예: 192.168.50.225 에 RDP 또는 직접):
ls D:\DEV\ComfyUI\input\test-mkdir-*
```

| 결과 | 1차 결정 | PLAN 의 폴백 |
|---|---|---|
| 200 + 새 디렉토리 자동 생성 | 설계 그대로 (`subfolder="asset-factory"` 디폴트) | 그대로 |
| 4xx 또는 root (`input/`) 에 떨어짐 | `subfolder=""` 디폴트 + 파일명 prefix `af_<sha>_<orig>` 로 격리 | PLAN §4 단계 0b 표 폴백 분기 적용 |

신규 세션이 사용자에게 결과 받아서 PLAN 본문의 코드 스켈레톤 (§4 단계 1) 의
디폴트 값을 확정한 다음 단계 1 부터 진입.

---

## 4. 확정 결정 사항 (이전 세션에서 사용자 컨펌됨)

PLAN §10 의 모든 보류 항목 + 비판 검토 보완 3건 모두 채택:

- [x] **subfolder 디폴트** = `"asset-factory"` (단계 0b 결과에 따라 폴백)
- [x] **파일명** = `<sha256[:12]>_<safe_original>.<ext>` — `[a-zA-Z0-9._-]` whitelist + 그 외 `_` 치환
- [x] **CLI 1차 제외** — `af workflow upload` 같은 wrapper 는 별도 PR 로 빠짐. 1차는 curl 2-step
- [x] **GC 1차 미구현** — 후속 [TODOS.md](./TODOS.md) 의 `comfyui-inputs-gc` 항목
- [x] **PIL `Image.verify()` 디코딩 검증 1차 포함** — content-type whitelist 만으로는 polyglot 위험. PIL `Image.open(BytesIO).verify()` + `format in {PNG,JPEG,WEBP}` 추가
- [x] **`patch_workflow` 를 `load_images: dict` generic 으로 1차 도입** — backward-compat `pose_image` kwarg 별칭 유지. 미래 LoadImage 라벨 추가 시 N→∞ 비용 = 0
- [x] **subfolder mkdir 폴백 분기** — 단계 0b 결과 기반

---

## 5. 코드 스켈레톤 — 그대로 복붙 가능 (PLAN §4 에서 발췌)

PLAN 본문에 이미 코드 스켈레톤이 있지만, 신규 세션이 빠르게 픽업하도록 핵심
3개만 여기 응축:

### 5.1 `_safe_subfolder` (단계 1)

```python
# server.py — _safe_segment 옆에 추가
import re, hashlib

_SAFE_SUBFOLDER_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
_SAFE_FILENAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]")


def _safe_subfolder(value: str | None) -> str:
    """ComfyUI input/<subfolder> 검증. 위반 시 'asset-factory' 로 정규화."""
    if not value:
        return "asset-factory"
    cleaned = value.strip().lstrip("/\\")
    if not _SAFE_SUBFOLDER_RE.fullmatch(cleaned) or ".." in cleaned:
        return "asset-factory"
    return cleaned


def _safe_input_filename(original: str, content_bytes: bytes) -> str:
    """업로드 파일명 안정화. <sha256[:12]>_<safe_original>.<ext>."""
    digest = hashlib.sha256(content_bytes).hexdigest()[:12]
    stem, dot, ext = (original or "input.png").rpartition(".")
    if not dot:
        stem, ext = original or "input", "png"
    safe_stem = _SAFE_FILENAME_CHARS.sub("_", stem) or "input"
    safe_ext = _SAFE_FILENAME_CHARS.sub("_", ext) or "png"
    return f"{digest}_{safe_stem[:64]}.{safe_ext[:8]}"
```

### 5.2 `ComfyUIClient.upload_input_image` (단계 2)

```python
# generator_comfyui.py — submit() 직전 또는 직후
async def upload_input_image(
    self,
    image_bytes: bytes,
    filename: str,
    subfolder: str = "asset-factory",
    overwrite: bool = True,
) -> dict[str, str]:
    url = f"{self.base_url}/upload/image"
    form = aiohttp.FormData()
    form.add_field("image", image_bytes, filename=filename, content_type="application/octet-stream")
    form.add_field("subfolder", subfolder)
    form.add_field("type", "input")
    form.add_field("overwrite", "true" if overwrite else "false")

    last_exc: Exception | None = None
    backoff = 1.0
    for attempt in range(1, self.retries + 1):
        try:
            session = await self._get_session()  # PR #13 의 lazy session 재사용
            async with session.post(url, data=form) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise SDError(
                        body[:1500] or f"HTTP {resp.status}",
                        code=_classify_comfy_failure(resp.status, body),
                        http_status=resp.status,
                    )
                return await resp.json()
        except SDError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            last_exc = exc
            if attempt == self.retries:
                code = "timeout" if isinstance(exc, asyncio.TimeoutError) else "unreachable"
                raise SDError(str(exc) or "upload failed", code=code) from exc
            await asyncio.sleep(backoff)
            backoff *= 2
    raise SDError("upload_image 실패", code="unreachable") from last_exc
```

### 5.3 `POST /api/workflows/inputs` (단계 3) — PIL verify 필수

```python
# server.py — /api/workflows/generate 위쪽에 추가
from io import BytesIO
from fastapi import UploadFile, Form
from PIL import Image, UnidentifiedImageError

MAX_INPUT_BYTES = 20 * 1024 * 1024
_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}


@app.post("/api/workflows/inputs", dependencies=[Depends(require_api_key)])
async def upload_workflow_input(
    file: UploadFile,
    subfolder: str = Form("asset-factory"),
) -> dict[str, str]:
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(415, f"지원 안 되는 content-type: {file.content_type}")
    bytes_ = await file.read()
    if len(bytes_) > MAX_INPUT_BYTES:
        raise HTTPException(413, f"이미지 너무 큼: {len(bytes_)} > {MAX_INPUT_BYTES}")
    if len(bytes_) == 0:
        raise HTTPException(400, "빈 파일")

    # PIL 디코딩 검증 — content-type 위장 polyglot 차단
    try:
        with Image.open(BytesIO(bytes_)) as img:
            img.verify()
            actual_format = img.format
    except (UnidentifiedImageError, Exception) as exc:
        raise HTTPException(400, f"이미지 디코딩 실패: {exc}") from exc
    if actual_format not in _ALLOWED_FORMATS:
        raise HTTPException(415, f"지원 안 되는 포맷: {actual_format}")

    return await comfyui_client.upload_input_image(
        image_bytes=bytes_,
        filename=_safe_input_filename(file.filename or "input.png", bytes_),
        subfolder=_safe_subfolder(subfolder),
    )
```

### 5.4 `workflow_patcher` `_LOAD_IMAGE_RULES` 분리 + `load_images` (단계 5)

```python
# workflow_patcher.py
_LOAD_IMAGE_RULES: dict[str, _PatchRule] = {
    "pose_image": _PatchRule(
        class_type="LoadImage",
        title_match=r"^Pose grid$",   # anchor 강제
        apply=_set_input("image"),
    ),
    "source_image": _PatchRule(
        class_type="LoadImage",
        title_match=r"^Load source image$",   # 단계 0 결과 (PoseExtract_V37)
        apply=_set_input("image"),
    ),
    # 향후 i2i source / mask / reference 등 추가 시 한 줄
}
# 기존 _RULES 에서 "pose_image" 항목 제거 — 위로 이전


def patch_workflow(
    api_json,
    *,
    # ... 기존 12 kwargs + ksampler_overrides ...
    pose_image: str | None = None,                  # backward compat 별칭
    load_images: dict[str, str] | None = None,      # 신규 generic 채널
):
    wf = copy.deepcopy(api_json)
    applied: dict[str, list[str]] = {}
    skipped: list[str] = []

    # 1) 기존 스칼라 규칙들 (prompt/seed/cfg/...) 그대로
    # ... 기존 로직 ...

    # 2) LoadImage dispatch — backward-compat 합성
    effective_load_images: dict[str, str] = dict(load_images or {})
    if pose_image is not None and "pose_image" not in effective_load_images:
        effective_load_images["pose_image"] = pose_image

    for label, image_name in effective_load_images.items():
        rule = _LOAD_IMAGE_RULES.get(label)
        if rule is None:
            skipped.append(label)
            continue
        matches = find_nodes(wf, rule.class_type, title_match=rule.title_match)
        if not matches:
            skipped.append(label)
            continue
        applied[label] = []
        for nid, node in matches:
            inputs = node.setdefault("inputs", {})
            rule.apply(inputs, image_name)
            applied[label].append(nid)

    # 3) ksampler_overrides 처리 (PR #13 에서 이미 추가됨, 그대로)
    return wf, PatchReport(applied=applied, skipped=skipped)
```

---

## 6. 테스트 패턴 — 그대로 복붙 가능

### 6.1 aioresponses 통합 테스트 패턴 (단계 2)

이미 [tests/test_generator_comfyui.py](../tests/test_generator_comfyui.py) 에 13
케이스 존재 — `submit_and_wait` 풀 시퀀스부터 503/504/timeout/prompt_lost 까지.
새 `upload_input_image` 도 같은 패턴:

```python
def test_upload_input_image_full_http_sequence() -> None:
    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    payload={"name": "x.png", "subfolder": "asset-factory", "type": "input"},
                )
                resp = await client.upload_input_image(
                    image_bytes=b"PNG_BYTES", filename="x.png",
                )
                assert resp["name"] == "x.png"
    asyncio.run(scenario())


def test_upload_413_oversized_classified_as_sd_client_error() -> None:
    # 5xx → sd_server_error / 4xx → sd_client_error 분류 회귀 테스트
    ...
```

`_client()` 헬퍼 ([tests/test_generator_comfyui.py:18](../tests/test_generator_comfyui.py:18)) 그대로 사용 가능.

### 6.2 multipart endpoint 테스트 패턴 (단계 3)

[tests/test_workflow_endpoints.py](../tests/test_workflow_endpoints.py) 의 fixture
`isolated` 사용 — `monkeypatch.setattr(server, "comfyui_client", fake)` 로 삽입.

```python
def test_upload_endpoint_rejects_polyglot(isolated) -> None:
    """PNG 헤더 위장 ZIP → PIL verify 실패 → 400."""
    polyglot = b"\x89PNG\r\n\x1a\nPK\x03\x04..."  # PNG signature + ZIP body
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("evil.png", polyglot, "image/png")},
            data={"subfolder": "asset-factory"},
            headers={"X-API-Key": "..."},
        )
    assert r.status_code == 400
```

### 6.3 mock 시그니처 호환

PR #13 에서 server 함수 시그니처 변경 흡수를 위해 fixture 의 monkeypatch 가
가변 인자를 받도록 통일됨:

```python
monkeypatch.setattr(
    server, "_ensure_disk_space_for_enqueue", lambda *_a, **_kw: None,
)
```

신규 엔드포인트도 같은 패턴 따를 것.

---

## 7. 개발 환경

### 7.1 Python / venv

| 항목 | 값 |
|---|---|
| OS | Windows 11 Pro |
| Shell | bash (Unix syntax) + PowerShell (Windows tool 호출용) |
| Primary venv | `D:\DEV\asset-factory\.venv` (Python 3.14.4 — `requirements.txt` 가 3.11 가정인데 cp314 wheel 로 정상 설치됨) |
| 추가 보유 | Python 3.10 (`python3.10 --version`) — 미사용. CI 정합성 (3.11/3.12) 위해 향후 별도 venv 검토 가능 |
| 패키지 | `requirements.txt` 에 `aioresponses==0.7.8` 이미 추가됨 (PR #13). `ruff` 는 venv 에 설치돼 있음 |

### 7.2 명령 패턴

```powershell
# pytest
.\.venv\Scripts\python.exe -m pytest tests/ -q

# 일부만
.\.venv\Scripts\python.exe -m pytest tests/test_generator_comfyui.py -v

# ruff
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff check . --fix
```

### 7.3 gh CLI

| 위치 | 비고 |
|---|---|
| `C:\Program Files\GitHub CLI\gh.exe` | winget 설치 (시스템 표준) |
| `C:\Users\a\AppData\Local\gh-cli\bin\gh.exe` | 수동 zip (백업) |
| 인증 | 이미 완료 (사용자가 `gh auth login` 처리) |

### 7.4 git identity

**CLAUDE.md 의 `Never update git config` 정책 준수 — `git config` 절대 호출
금지.** 한 번의 commit 에만 identity 적용:

```bash
git -c user.email="cisisn@gmail.com" -c user.name="sunghere" commit -m "..."
```

---

## 8. CI 게이트 — 깨면 머지 차단

`.github/workflows/ci.yml` 가 Python **3.11 + 3.12** 매트릭스로 ubuntu-latest
에서 실행. 단계는:

1. `pip install -r requirements.txt && pip install ruff`
2. `ruff check .` (실패 = 즉시 차단)
3. `pytest tests/ -q --maxfail=1 --disable-warnings`

**Ruff 가 잡는 흔한 신규 코드 함정:**
- **F401**: 미사용 import — 신규 파일 작성 시 자주 발생
- **F541**: f-string 안에 placeholder 없음 (`f"\n=== 결과 ==="` 같은 것)
- **B007**: for 루프 변수 미사용 (사용 안 하면 `_var` prefix)
- **E741**: 모호 변수명 (`I`, `l`, `O` 등 단일 문자 — `INDENT` 같이)

CodeQL `Analyze (python)` 도 별도 워크플로우로 도는데 P0 알람만 머지 차단.
no-effect statement (`...`) 는 false positive 라 docstring 으로 우회 — PR #13
의 `sd_backend.py:88,91` 처럼.

---

## 9. Git 워크플로우 템플릿

```bash
# 1. main 동기화
cd D:/DEV/asset-factory
git checkout main
git pull origin main

# 2. 새 브랜치
git checkout -b feat/comfyui-dynamic-inputs

# 3. 작업 후 stage
git add -A

# 4. commit (identity 인라인)
git -c user.email="cisisn@gmail.com" -c user.name="sunghere" commit -m "$(cat <<'EOF'
feat: ComfyUI 동적 입력 업로드 (POST /api/workflows/inputs)

- _safe_subfolder / _safe_input_filename 헬퍼
- ComfyUIClient.upload_input_image() (multipart)
- POST /api/workflows/inputs (PIL verify + size 가드)
- POST /api/workflows/inputs/from-asset
- workflow_patcher load_images dict generic 디스패치 + _LOAD_IMAGE_RULES 분리
- backward-compat: pose_image kwarg 별칭 유지
- curl 2-step 시나리오 문서화 (docs/COMFYUI_DYNAMIC_INPUTS.md)

상세: docs/PLAN_comfyui_dynamic_inputs.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

# 5. push + PR
git push -u origin feat/comfyui-dynamic-inputs
& "C:\Program Files\GitHub CLI\gh.exe" pr create \
    --title "feat: ComfyUI 동적 입력 업로드" \
    --body "..." \
    --base main \
    --head feat/comfyui-dynamic-inputs
```

---

## 10. 직전 세션의 미완 (loose ends)

### 10.1 `workflows/README.md` 에 in-flight C-P0 #2 편집 (uncommitted)

직전 세션이 [TODOS.md](./TODOS.md) 의 C-P0 #2 (workflows/README.md:128 의 `af
workflow gen` 광고 정정) 작업 중 인터럽트로 중단. 3개 hunk 가 working tree 에
남아 있을 수 있음:

- 상단 CLI 미구현 배너 추가
- §3-§4 의 `af workflow ...` 예제를 curl 로 교체
- §"새 카테고리/변형" 의 `af workflow catalog` 한 줄 정정

**처리 옵션 (신규 세션이 선택):**
1. **그대로 유지 + B 작업 중에 이 README 수정도 같은 PR 에 묶음** (권장 — 어차피 B
   작업이 `workflows/README.md` 에 curl 예제 추가하므로 자연스럽게 합쳐짐)
2. **별도 작은 PR 로 먼저 처리** (5분) — clean slate 에서 B 시작
3. **revert 후 B 끝나고 다시** — `git checkout workflows/README.md`

### 10.2 워크트리 정리 (사용자가 외부에서)

직전 세션이 `claude/vigorous-torvalds-a1e1af` 워크트리 안에서 돌고 있어
삭제 못 함. 사용자가 본인 터미널에서:

```bash
cd D:/DEV/asset-factory
git worktree remove --force .claude/worktrees/vigorous-torvalds-a1e1af
git branch -D claude/vigorous-torvalds-a1e1af
```

(이미 `.gitignore` 에 `.claude/` 가 들어가 있어 안 지워도 향후 커밋 오염 0)

---

## 11. 본 작업 외 (TODOS.md)

[`docs/TODOS.md`](./TODOS.md) 의 항목들은 본 PR 범위 밖. 특히:

- **P0** ComfyUI input/ GC cron — 본 PR 머지 후 디스크 폭주 위험 → 시급 후속
- **P1** `af.mjs` CLI 신설 (~2h) — 본 PR 의 curl 2-step 을 wrapper 로 감쌈
- **P1** task → task chain 자동화 (DB 마이그레이션 필요) — PoseExtract 결과를 다음 변형 입력으로 자동
- **P1** PoseExtract 전용 화면 (`docs/SCREEN_SPEC_v0.2.md` 미반영)

본 PR 끝나면 이 중 하나 픽업 권장.

---

## 12. 빠른 시작 체크리스트 (신규 세션이 픽업할 때)

- [ ] [docs/PLAN_comfyui_dynamic_inputs.md](./PLAN_comfyui_dynamic_inputs.md) 읽기 (10분)
- [ ] 본 핸드오프 §3 의 단계 0b 결과를 사용자에게 받기 — 폴백 분기 결정
- [ ] §10.1 의 README in-flight 처리 방향 결정
- [ ] `git checkout main && git pull` → 최신 동기 확인 (`4de9fbf` 또는 그 이상)
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/ -q` → **202 passed** 확인
- [ ] 새 브랜치 `feat/comfyui-dynamic-inputs` 따고 §2 의 8단계대로 진행
- [ ] 단계마다 pytest + ruff 통과 확인
- [ ] PR 본문에 본 핸드오프 + PLAN 링크 포함

질문 / 막힘 시 사용자에게: "PLAN §X 의 Y 결정이 필요" 형태로 정확히 물어볼 것.

---

**참고**

- [docs/PLAN_comfyui_dynamic_inputs.md](./PLAN_comfyui_dynamic_inputs.md) — 본 작업 상세 계획 (코드 스켈레톤 + 테스트 매트릭스 + 위험 표)
- [docs/TODOS.md](./TODOS.md) — 후속 작업 추적 (P0/P1/P2)
- [PR #13](https://github.com/sunghere/asset-factory/pull/13) — 본 작업의 인프라 베이스
- [CLAUDE.md](../CLAUDE.md) — 프로젝트 운영 규칙 (특히 NEXT.md 핸드오프 규칙)
- [HANDOFF.md](../HANDOFF.md) — 인프라/서버 주소/제약 (192.168.50.225 ComfyUI 등)

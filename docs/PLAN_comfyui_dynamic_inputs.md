# PLAN — ComfyUI 동적 입력 이미지 업로드 (상세)

> 상태: **1차 PR 준비 완료, 미구현**
> 작성: 2026-04-26 (초안) → 2026-04-26 (상세화·검토 반영)
> 대상 PR: 1개, ~3.0 시간
> 후속 작업 추적: [`docs/TODOS.md`](./TODOS.md) — `comfyui-inputs` 섹션

---

## 0. 한 줄 요약

asset-factory 가 임의 이미지를 ComfyUI 의 `input/` 디렉토리에 업로드해 LoadImage 노드가
참조 가능하게 만든다. 1차는 multipart 직업로드 + 기존 asset 참조 두 경로 + workflow_patcher
의 generic `load_images` 디스패치.

---

## 1. 왜 필수인가

레지스트리에 등록된 변형 중 동적 입력이 없으면 작동 못 하는 것들:

| 변형 | 필요한 입력 | 현재 |
|---|---|---|
| `sprite/pose_extract` | 사용자 임의 캐릭터 이미지 → OpenPose stick figure 추출 | **무용지물** (`your_image.png` 디폴트) |
| ControlNet 변형 (`sprite/pixel_alpha` 등) | 사용자 정의 pose grid (다른 등신/view 수) | 정해진 grid 만 |
| 향후 i2i 변형 | source image | 추가 불가 |

PoseExtract 는 **사용자 캐릭터 이미지 → pose 추출 → 다른 워크플로우의 ControlNet 입력으로
재사용** chain 의 시작점.

---

## 2. 아키텍처

### 옵션 비교 (재확인)

| 옵션 | 메커니즘 | 평가 |
|---|---|---|
| **A** | ComfyUI `POST /upload/image` 통과 | ⭐ 표준 API, 결합도 낮음 |
| B | NFS/SMB 마운트 (asset-factory 가 ComfyUI input/ 직접 쓰기) | ❌ 운영 복잡, dev/prod divergence |
| C | 사전 업로드만 (현 상태) | 동적 입력 불가 |

**선택: A.** 같은 호스트 (개발 PC) / 별도 호스트 ([HANDOFF.md](../HANDOFF.md) 의 192.168.50.225)
어느 쪽이든 동일 동작.

### ComfyUI `/upload/image` API

```
POST /upload/image
Content-Type: multipart/form-data
  image: <file>          # PNG/JPG/WEBP bytes
  subfolder: <string>    # optional, default ""
  type: input            # 'input' | 'temp'
  overwrite: true|false  # default false → 충돌 시 (1).png suffix

200 응답:
{ "name": "file.png", "subfolder": "asset-factory", "type": "input" }
```

응답의 `name` (또는 `subfolder/name`) 을 LoadImage 노드의 `image` 필드에 set.

---

## 3. 데이터 입력 경로

| 경로 | 사용처 | 1차 구현 |
|---|---|---|
| (a) bytes 직업로드 | 사용자 PC 의 PNG → curl 또는 후속 CLI | `POST /api/workflows/inputs` multipart |
| (b) 내부 asset 참조 | 이전 generate 결과 → 다음 ControlNet 입력 | `POST /api/workflows/inputs/from-asset` |
| (c) URL fetch | Slack/Drive 외부 URL | ❌ 1차 범위 외 ([TODOS.md](./TODOS.md)) |

---

## 4. 단계별 구현

### 단계 0 — PoseExtract title 사전 검증 (완료 ✓)

```
$ grep -n '"title"' workflows/sprite/PoseExtract_V37_api.json | head -3
8:      "title": "Load source image"
```

→ `title_match=r"^Load source image$"` (anchor 강제) 로 정규식 확정.
다른 sprite 워크플로우 9개의 LoadImage 는 모두 `"Pose grid"` title 이라 충돌 없음.

### 단계 0b — ComfyUI subfolder mkdir 동작 실측 (5min, 사용자 수행)

ComfyUI 가 존재하지 않는 subfolder 를 자동 생성하는지 확인. 결과에 따라 fallback 분기 결정.

```bash
# 1) 새 subfolder 로 한 번 쳐보기
curl -F image=@some_test.png \
     -F subfolder=test-mkdir-$(date +%s) \
     -F type=input \
     -F overwrite=true \
     http://192.168.50.225:8188/upload/image

# 2) Windows ComfyUI 호스트에서 확인
ls D:\DEV\ComfyUI\input\test-mkdir-*
```

| 결과 | 후속 처리 |
|---|---|
| 200 + 새 디렉토리 자동 생성 | 설계 그대로 (`subfolder="asset-factory"` 디폴트) |
| 4xx 또는 root 에 떨어짐 | 1차는 `subfolder=""` + 파일명 prefix `af_<sha>_<orig>` 로 격리 |

### 단계 1 — `_safe_subfolder()` + `_safe_input_filename()` 헬퍼 (20min)

[server.py:122-127](../server.py:122) 의 `_safe_segment` 옆에 두 헬퍼 추가.

```python
_SAFE_SUBFOLDER_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
_SAFE_FILENAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]")


def _safe_subfolder(value: str | None) -> str:
    """ComfyUI input/<subfolder> 검증.

    - 빈 문자열 / None → 디폴트 ``asset-factory``
    - ``..``, 절대경로, NUL byte, 64자 초과 → 디폴트로 정규화
    - 허용: ``[a-zA-Z0-9._-]{1,64}``
    """
    if not value:
        return "asset-factory"
    cleaned = value.strip().lstrip("/\\")
    if not _SAFE_SUBFOLDER_RE.fullmatch(cleaned) or ".." in cleaned:
        return "asset-factory"
    return cleaned


def _safe_input_filename(original: str, content_bytes: bytes) -> str:
    """업로드 파일명 안정화 (멱등 + 안전).

    - 디스플레이 이름은 ``[a-zA-Z0-9._-]`` whitelist, 그 외 ``_`` 치환
    - 충돌/캐시 hit 검출 위해 ``<sha256[:12]>_<safe_original>``
    - 확장자 없으면 ``.png`` 디폴트
    """
    digest = hashlib.sha256(content_bytes).hexdigest()[:12]
    stem, dot, ext = (original or "input.png").rpartition(".")
    if not dot:
        stem, ext = original or "input", "png"
    safe_stem = _SAFE_FILENAME_CHARS.sub("_", stem) or "input"
    safe_ext = _SAFE_FILENAME_CHARS.sub("_", ext) or "png"
    return f"{digest}_{safe_stem[:64]}.{safe_ext[:8]}"
```

**테스트 (parametrize)**: `tests/test_safe_path_helpers.py` 신규
- `_safe_subfolder`: `""`, `None`, `..`, `/etc/passwd`, `\x00`, `a`*100, `valid-name`, `한글` → 각 기대값
- `_safe_input_filename`: `"foo.png"+b"x"` → `"<sha>_foo.png"`, `"한글.png"` → `"<sha>_____.png"` (sanitize), 확장자 없음, 빈 입력

### 단계 2 — `ComfyUIClient.upload_input_image()` (35min)

[generator_comfyui.py:87](../generator_comfyui.py:87) `ComfyUIClient` 에 메서드 추가. 기존
[generator_comfyui.py:269](../generator_comfyui.py:269) `_request_json` 의 retry/backoff 패턴
재사용. 에러 분류는 [generator_comfyui.py:70](../generator_comfyui.py:70) `_classify_comfy_failure`
경로로 통일.

```python
async def upload_input_image(
    self,
    image_bytes: bytes,
    filename: str,
    subfolder: str = "asset-factory",
    overwrite: bool = True,
) -> dict[str, str]:
    """multipart/form-data 로 ComfyUI ``/upload/image`` POST.

    반환: ``{"name": "...", "subfolder": "...", "type": "input"}``.
    실패 시 ``SDError`` (code='unreachable'|'sd_server_error'|'timeout').
    """
    url = f"{self._base_url}/upload/image"
    form = aiohttp.FormData()
    form.add_field("image", image_bytes, filename=filename, content_type="application/octet-stream")
    form.add_field("subfolder", subfolder)
    form.add_field("type", "input")
    form.add_field("overwrite", "true" if overwrite else "false")

    last_exc: Exception | None = None
    for attempt in range(self._retries):
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, data=form) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise SDError(
                            code=_classify_comfy_failure(resp.status, body),
                            message=f"upload_image {resp.status}: {body[:200]}",
                        )
                    return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            await asyncio.sleep(self._backoff_seconds(attempt))
    raise SDError(code="unreachable", message=f"upload_image: {last_exc}")
```

**테스트**: `tests/test_comfyui_client_upload.py` (신규)
- aiohttp 응답 mock — 200, 503 (`sd_server_error`), 타임아웃 (`timeout`), 연결 거부 (`unreachable`)
- FormData 송신 필드 검증 (`image`/`subfolder`/`type`/`overwrite`)
- 재시도 횟수 검증 (3회 후 마지막 exc)

### 단계 3 — `POST /api/workflows/inputs` (35min)

[server.py](../server.py) 의 기존 `/api/workflows/*` 영역에 추가. **PIL 디코딩 검증 필수**.

```python
from io import BytesIO
from fastapi import UploadFile, Form, Depends, HTTPException
from PIL import Image, UnidentifiedImageError

MAX_INPUT_BYTES = 20 * 1024 * 1024  # 20MB
_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}


@app.post("/api/workflows/inputs", dependencies=[Depends(require_api_key)])
async def upload_workflow_input(
    file: UploadFile,
    subfolder: str = Form("asset-factory"),
) -> dict[str, str]:
    """multipart 로 받은 이미지를 ComfyUI input/<subfolder>/ 에 업로드.

    응답의 'name' 을 후속 /api/workflows/generate 의
    ``workflow_params.<load_image_label>`` 에 사용.
    """
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(415, f"지원 안 되는 content-type: {file.content_type}")
    bytes_ = await file.read()
    if len(bytes_) > MAX_INPUT_BYTES:
        raise HTTPException(413, f"이미지 너무 큼: {len(bytes_)} > {MAX_INPUT_BYTES}")
    if len(bytes_) == 0:
        raise HTTPException(400, "빈 파일")

    # PIL 로 실제 디코딩 검증 — content-type 위장 polyglot 차단
    try:
        with Image.open(BytesIO(bytes_)) as img:
            img.verify()
            actual_format = img.format
    except (UnidentifiedImageError, Exception) as exc:
        raise HTTPException(400, f"이미지 디코딩 실패: {exc}")
    if actual_format not in _ALLOWED_FORMATS:
        raise HTTPException(415, f"지원 안 되는 포맷: {actual_format}")

    return await comfyui_client.upload_input_image(
        image_bytes=bytes_,
        filename=_safe_input_filename(file.filename or "input.png", bytes_),
        subfolder=_safe_subfolder(subfolder),
    )
```

**테스트**: `tests/test_workflow_inputs_endpoint.py` (신규)
| 케이스 | 기대 |
|---|---|
| 정상 PNG | 200 + ComfyUIClient mock 응답 통과 |
| content-type=`text/plain` | 415 |
| 파일 > 20MB | 413 |
| 빈 파일 | 400 |
| PNG 헤더 위장 ZIP | 400 (PIL verify 실패) |
| `subfolder=../etc` | `_safe_subfolder` → `asset-factory` 정규화 후 통과 |
| `require_api_key` 누락 | 401 |
| ComfyUIClient `unreachable` | 502 (또는 기존 SDError 매핑 그대로) |

### 단계 4 — `POST /api/workflows/inputs/from-asset` (25min)

```python
class FromAssetRequest(BaseModel):
    asset_id: str
    subfolder: str = "asset-factory"


@app.post("/api/workflows/inputs/from-asset", dependencies=[Depends(require_api_key)])
async def upload_workflow_input_from_asset(req: FromAssetRequest) -> dict[str, str]:
    asset = await db.get_asset(req.asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")

    img_path = _ensure_path_allowed(Path(asset["image_path"]))
    bytes_ = img_path.read_bytes()
    return await comfyui_client.upload_input_image(
        image_bytes=bytes_,
        filename=_safe_input_filename(img_path.name, bytes_),
        subfolder=_safe_subfolder(req.subfolder),
    )
```

**테스트**: 위 동일 파일에 추가
- 존재하는 asset → 200
- 존재 안 함 → 404
- `image_path` 가 allowlist 외 → 403 ([server.py:103](../server.py:103) 자동 처리)

### 단계 5 — workflow_patcher generic `load_images` 디스패치 (40min)

[workflow_patcher.py:97-158](../workflow_patcher.py:97) 의 `_RULES` 에서 LoadImage 항목들을
별도 `_LOAD_IMAGE_RULES` 로 분리. `patch_workflow()` 에 `load_images: dict[str, str] | None`
kwarg 추가, 기존 `pose_image` 는 backward-compat 별칭 유지.

```python
# workflow_patcher.py

# LoadImage 라벨별 매칭 규칙 — 추가 시 여기만 한 줄 추가
_LOAD_IMAGE_RULES: dict[str, _PatchRule] = {
    "pose_image": _PatchRule(
        class_type="LoadImage",
        title_match=r"^Pose grid$",  # ← anchor 강제 (다른 LoadImage 우발 매칭 방지)
        apply=_set_input("image"),
    ),
    "source_image": _PatchRule(
        class_type="LoadImage",
        title_match=r"^Load source image$",
        apply=_set_input("image"),
    ),
    # 향후 i2i source / mask / reference 등 추가 시 한 줄
}

# 기존 _RULES 에서 "pose_image" 항목 제거 (위로 이전)


def patch_workflow(
    api_json,
    *,
    # ... 기존 12 kwargs ...
    pose_image: str | None = None,                 # backward compat 별칭
    load_images: dict[str, str] | None = None,     # 신규 generic 채널
):
    wf = copy.deepcopy(api_json)
    applied: dict[str, list[str]] = {}
    skipped: list[str] = []

    # 1) 기존 스칼라 규칙들 (prompt/seed/cfg/...)
    # ... 기존 로직 그대로 ...

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

    # 3) lora_strengths 등 기존 특수 처리 ...

    return wf, PatchReport(applied=applied, skipped=skipped)
```

**테스트**: [tests/test_workflow_patcher.py](../tests/test_workflow_patcher.py) 보강
- `pose_image="x.png"` (backward compat) → `_LOAD_IMAGE_RULES["pose_image"]` 매칭 검증
- `load_images={"source_image": "y.png"}` → PoseExtract JSON 의 LoadImage 매칭 검증
- 두 라벨 동시 (`{"pose_image": "p", "source_image": "s"}`) → 각자 다른 노드에 적용
- `load_images={"unknown_label": "x"}` → `report.skipped` 에 라벨 포함, 에러 없음
- `pose_image` + `load_images={"pose_image": "y"}` 동시 → `load_images` 가 우선

### 단계 6 — curl 2-step 시나리오 + 문서화 (20min)

[workflows/README.md](../workflows/README.md) 또는 별도 `docs/COMFYUI_DYNAMIC_INPUTS.md`
신설. 사용자가 PoseExtract 첫 사용 시 따라할 흐름:

```bash
# 1) 사용자 캐릭터 이미지 업로드
curl -X POST http://localhost:8000/api/workflows/inputs \
  -H "X-API-Key: $AF_API_KEY" \
  -F file=@my_character.png \
  -F subfolder=asset-factory
# → {"name": "abc123def456_my_character.png", "subfolder": "asset-factory", "type": "input"}

# 2) 응답의 name 을 workflow_params.source_image (또는 load_images) 에 박아서 generate
curl -X POST http://localhost:8000/api/workflows/generate \
  -H "X-API-Key: $AF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "sprite/pose_extract",
    "project": "cat-raising",
    "asset_key": "ksh_baby_idle",
    "workflow_params": {
      "load_images": {"source_image": "abc123def456_my_character.png"}
    }
  }'

# 3) PoseExtract 결과를 다음 변형의 입력으로 chain (수동)
curl -X POST http://localhost:8000/api/workflows/inputs/from-asset \
  -H "X-API-Key: $AF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "<step2_생성된_asset_id>", "subfolder": "asset-factory"}'
# → 응답 name 을 다음 generate 의 load_images.pose_image 에 사용
```

---

## 5. 운영

### 5.1 명명 규칙 (멱등성)

`<sha256[:12]>_<safe_original>.<ext>` — 같은 bytes 재업로드 시 동일 이름. 디스크 절약 +
캐시 hit 검출. `safe_original` 은 `[a-zA-Z0-9._-]` whitelist 통과.

### 5.2 GC

**1차: 미구현.** 사용자가 ComfyUI 호스트에서 수동.
**후속**: [TODOS.md](./TODOS.md) `comfyui-inputs-gc` — `task.workflow_params_json` JSON 파싱
→ N일 미참조 파일 삭제 cron.

### 5.3 보안

| 위협 | 방어 |
|---|---|
| polyglot (PNG 헤더 + ZIP/PHP) | PIL `Image.verify()` + format whitelist |
| path traversal in subfolder | `_safe_subfolder` whitelist 정규식 |
| oversized upload | `MAX_INPUT_BYTES = 20MB` → 413 |
| 미인증 호출 | `Depends(require_api_key)` |
| asset 경로 우회 | `_ensure_path_allowed(asset["image_path"])` |
| content-type 위장 | content-type whitelist + PIL 디코딩 검증 (이중) |

---

## 6. 테스트 매트릭스

| 파일 | 신규/보강 | 케이스 수 | 검증 |
|---|---|---|---|
| `tests/test_safe_path_helpers.py` | 신규 | ~10 | `_safe_subfolder` / `_safe_input_filename` parametrize |
| `tests/test_comfyui_client_upload.py` | 신규 | ~6 | aiohttp mock, retry, error 분류 |
| `tests/test_workflow_inputs_endpoint.py` | 신규 | ~8 | multipart, content-type, size, PIL verify, 인증, from-asset |
| `tests/test_workflow_patcher.py` | 보강 | +5 | `load_images` dict, `pose_image` compat, unknown label skip |

---

## 7. 구현 순서 + 추정

| # | 단계 | 추정 | 의존 |
|---|---|---|---|
| 0 | PoseExtract title grep — **완료 ✓** | - | - |
| 0b | ComfyUI subfolder mkdir 실측 (사용자, curl 1회) | 5min | - |
| 1 | `_safe_subfolder` / `_safe_input_filename` + parametrize 테스트 | 20min | - |
| 2 | `ComfyUIClient.upload_input_image()` + 단위 테스트 (mock + 503/timeout) | 35min | - |
| 3 | `POST /api/workflows/inputs` (multipart + PIL verify) + 테스트 | 35min | 1, 2 |
| 4 | `POST /api/workflows/inputs/from-asset` + 테스트 | 25min | 1, 2 |
| 5 | workflow_patcher generic `load_images` + `_LOAD_IMAGE_RULES` 분리 + 테스트 | 40min | - |
| 6 | curl 2-step 문서화 (`docs/COMFYUI_DYNAMIC_INPUTS.md`) | 20min | 3, 4, 5 |
| 7 | 통합 smoke (사용자 — 실 ComfyUI 로 PoseExtract end-to-end) | 30min | 0b, 6 |
| | **합계** | **~3.5h / PR 1개** | |

---

## 8. 1차 PR 범위 외 (→ [`docs/TODOS.md`](./TODOS.md))

- `af.mjs` CLI 신설 (`af workflow upload`, `--upload local:key`)
- URL fetch 입력 경로 (Slack/Drive 외부 URL → input/)
- ComfyUI input/ GC cron
- task → task chain 자동화 (DB 마이그레이션 필요)
- UI 노출 ([docs/SCREEN_SPEC_v0.2.md](./SCREEN_SPEC_v0.2.md) 의 PoseExtract 화면 신설)
- A1111 백엔드 호환 검토 (현재 ComfyUI 전용)
- [workflows/README.md:128](../workflows/README.md) 의 `af workflow gen` 광고 정리 (CLI 부재)

---

## 9. 변경 영향 범위 (1차 PR)

| 영역 | 영향 |
|---|---|
| `/api/workflows/generate` 흐름 | **무영향** — `workflow_params.load_images` 키 추가만 |
| A1111 백엔드 | **무영향** — ComfyUI 전용 |
| DB 스키마 | **무영향** — 파일명만 `task.workflow_params_json` 안에 |
| cherry-pick UI | **무영향** — 결과 이미지 동일 |
| 기존 `pose_image` kwarg | **backward compat 유지** — 별칭으로 처리 |

---

## 10. 결정 확정 사항

- [x] subfolder 디폴트 = `asset-factory` (실측 결과에 따라 폴백)
- [x] 명명 = `<sha256[:12]>_<safe_original>.<ext>`
- [x] CLI 묶음 1차 제외 → 별도 PR
- [x] GC 1차 미구현, 후속에 task 참조 추적 cron
- [x] PIL `Image.verify()` 디코딩 검증 1차 포함
- [x] `patch_workflow` 를 `load_images: dict` generic 채널로 1차에 도입 (backward compat)

## 11. 잔여 위험

| 위험 | 확률 | 완화 |
|---|---|---|
| ComfyUI 가 subfolder 자동 mkdir 안 함 | 미상 | 단계 0b 사전 실측 + fallback `subfolder=""` 분기 |
| sha256[:12] 충돌 | 10⁻¹¹ | 1만 파일/세션 기준 무시 가능 |
| PIL verify 가 모든 polyglot 잡지 못함 | 낮음 | content-type whitelist 와 이중 방어 |
| `_LOAD_IMAGE_RULES` 의 anchor 정규식이 미래 워크플로우 title 변경 시 silent skip | 중 | `report.skipped` 검사를 통합 smoke 에 포함 |

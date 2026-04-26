# TODOS for SKILL — Server Gaps Blocking the Ideal Asset-Factory Skill

> 이 문서는 **TDD red phase** 다. `~/workspace/sincerity-skills/asset-factory-api/SKILL.md`
> 를 "이상적 사용 경험" 기준으로 먼저 쓰고, 그 스킬이 가정하는 서버 인터페이스를 여기에
> 역추적해 적었다. 항목이 채워지면 스킬은 추가 변경 없이 바로 동작한다.
>
> **컨텍스트**: PR #14 (`feat/comfyui-dynamic-inputs`) 시점. A1111 직접 호출(SD txt2img/img2img)
> 멘탈모델은 폐기됐고, ComfyUI 워크플로우 변형 선택이 유일한 호출 경로다. asset-relay-agent
> 등 Paperclip 사내 에이전트가 이 스킬 한 장으로 모든 에셋 생성을 처리하는 게 목표.
>
> 문서 위치: `docs/TODOS.md`(일반 후속) 와 분리. 이건 **스킬-관점 갭**만 모은 좁은 트래커.

---

## 우선순위 범례

- **P0** — 이게 없으면 스킬이 거짓말이 됨. 머지 전 반드시 채워야 함.
- **P1** — 스킬이 동작은 하지만 에이전트 인지 부담 큼. 우회 가능하지만 거칠다.
- **P2** — 운영 편의. 빠져도 스킬은 견딜 만함.

각 항목은 **갭(현재 한계)** → **스킬이 이상적이라고 가정하는 인터페이스** → **필요한 서버 변경** 순.

---

## P0 — 머지 전 차단 항목

### 1. Bypass(승인 우회) 모드 — 인간 cherry-pick 단계를 건너뛰는 워크플로우 호출

**갭**:
asset-factory는 현재 모든 결과를 *candidate → approved* 흐름으로 흘린다. 승인 전엔 export
대상이 아니고, 승인 후에만 `--manifest`로 묶을 수 있다. 그러나 다음 시나리오에선 이 흐름이
오히려 짐이다:
- 로고/캐릭터 임시 시뮬레이션 (100장 일괄 — 사람 검수 무의미)
- 스캐치/씬 작업용 1회성 일러스트 (게임 내 자산이 아닌 임시물)
- 다른 에이전트의 입력으로 흘려보낼 중간 산출물 (PoseExtract → ControlNet chain 의 중간)

지금은 위 케이스에서도 검토 큐에 쌓여 사람의 의사결정을 강제한다. 자동화 파이프라인이
멈추거나 human-in-the-loop 의 의미가 희석된다.

**스킬이 가정하는 인터페이스**:
```bash
# 승인 절차 없이 즉시 결과를 회수하는 모드
af workflow gen sprite/pixel_alpha tmp_sketch sketch_001 "..." \
   --bypass-approval --wait
# → 결과 N장이 즉시 반환 가능 상태. approval 큐를 거치지 않음.

af list tmp_sketch --include-bypassed       # 일반 list 와 분리된 가시성
af get <asset_id> -o out.png                # 동일하게 접근
```

REST 레벨:
```http
POST /api/workflows/generate
{
  "category": "sprite",
  "variant": "pixel_alpha",
  ...
  "approval_mode": "bypass"   // "manual" (default) | "bypass" | "auto"
}
```

**필요한 서버 변경**:
- `runs` / `assets` 스키마에 `approval_mode` 컬럼 (또는 `bypassed: bool`).
- `bypass` 로 생성된 에셋은 별도 namespace (예: `tmp_*` project prefix 또는 명시 플래그)
  로 격리 — 사람 승인 큐에 노출되지 않게.
- export/manifest 로직: bypass 자산은 manifest 에 들어가지 않는다 (의도된 임시물).
- 보존 정책: bypass 에셋은 N일 후 GC (cron). 일반 candidate 보다 짧게.
- API key 인증은 그대로 (`Depends(require_api_key)`) — *승인* 우회지 *인증* 우회 아님.
- UI: bypass run 은 candidate 그리드에 안 뜨거나 별도 탭 ("Sandbox") 으로.

**스킬 사용 시나리오**:
- *케이스 A* — 로고 8장 cherry-pick: `--bypass-approval` **빼고** 호출 → 사람 승인 강제.
- *케이스 B* — 캐릭터 시뮬 100장: `--bypass-approval` 켜고 100 candidates → 자동 다운로드.
- *케이스 C* — chain 중간물 (PoseExtract 결과를 ControlNet 입력으로): bypass 로 생성 후
  바로 `from-asset` 으로 다음 단계 입력에 투입.

---

### 2. 변형별 load_images 라벨이 catalog 응답에 명시 노출

**갭**:
`GET /api/workflows/catalog` 응답에서 변형의 `defaults` 에 `pose_image` / `source_image`
같은 키가 *우연히* 노출돼 있어 라벨을 추측은 가능하다. 그러나:
- 라벨이 *기본값을 가질 때만* 노출됨 (기본값 없으면 어떤 라벨을 받을 수 있는지 보이지 않음).
- 라벨이 옵셔널인지 필수인지 catalog 만 봐서는 모름.
- 새 라벨 (`_LOAD_IMAGE_RULES` 의 키) 이 추가돼도 catalog 에 신호가 없다.

**스킬이 가정하는 인터페이스**:
```jsonc
// GET /api/workflows/catalog 응답
{
  "categories": {
    "sprite": {
      "variants": {
        "pixel_alpha": {
          // 신규 — 이 변형이 받을 수 있는 동적 입력 라벨
          "input_labels": [
            {
              "label": "pose_image",
              "required": false,
              "default": "pose_grid_1x3_mini_2.5h_1280x640.png",
              "description": "Pose grid (ControlNet OpenPose 입력)"
            }
          ],
          ...
        }
      }
    }
  }
}
```

**필요한 서버 변경**:
- `workflow_registry.py` 의 `VariantSpec` 에 `input_labels: list[InputLabelSpec]` 추가.
- `_LOAD_IMAGE_RULES` 와 변형의 워크플로우 JSON 을 교차 점검 (matching node 가 실제로
  존재하는 라벨만 노출).
- `to_catalog()` 출력에 `input_labels` 필드 추가.
- 라벨별 메타 (description / required / default) 는 `registry.yml` 에 변형별 옵셔널 섹션으로
  선언 가능하게.

---

### 3. CLI: `af workflow upload` (현재 curl 2-step 강제)

**갭**:
PR #14 의 `POST /api/workflows/inputs` / `/inputs/from-asset` 가 추가됐지만 **CLI 미구현**.
스킬이 추천하는 방법이 결국 `curl -F file=@... <URL>/api/workflows/inputs` 인데, 이건:
- API key 환경변수 (`AF_API_KEY`) 의존성을 사용자가 인지해야 함.
- 응답 JSON 파싱(`jq -r .name`) 을 사용자에게 시킴.
- 에러 메시지가 HTTP 상태에 묶여 컨텍스트 없음.

**스킬이 가정하는 인터페이스**:
```bash
# 단일 업로드 — name 만 echo
af workflow upload ./pose.png
# → asset-factory_xxxxxx_pose.png

# from-asset chain
af workflow upload --from-asset <asset_id>
# → asset-factory_xxxxxx_<asset>.png

# generate 와 한 번에 (가장 자주 쓰일 패턴)
af workflow gen sprite/pose_extract proj key "..." \
   --input source_image=@./pose.png \
   --input pose_image=<asset_id> \
   --wait
# 내부적으로 upload 먼저 → name 받아 load_images dict 에 박아 generate 호출.
```

**필요한 서버 변경**:
- 서버 변경 0. CLI 측 신규 명령만:
  - `af workflow upload <path|--from-asset id> [--subfolder]`
  - `af workflow gen ... --input <label>=<path|asset_id|@local>` (label 자동 매핑)
- CLI 가 catalog 의 신규 `input_labels` (P0-2) 를 봐서 unknown 라벨 시 친절한 에러.

---

## P1 — 인지 부담 감소

### 4. 변형별 권장 negative preset 을 catalog 응답에 명시

**갭**:
`registry.yml` 의 `presets:` (예: `NEG_PIXEL_SPRITE`) 는 변형 `defaults` 에서 `${preset:NEG_PIXEL_SPRITE}`
로 참조 가능하지만, catalog API 는 *이미 치환된 결과* 만 보여줄지 *raw 참조* 를 보여줄지 검증 필요.
스킬이 negative prompt 표준을 안내하지만, 변형별로 어떤 preset 이 적용되는지를 사용자가
알기 어렵다.

**스킬이 가정하는 인터페이스**:
catalog 응답에 변형별 `recommended_negative_preset` 키 (또는 `defaults.negative_prompt`
가 항상 채워져 있음) 보장. 스킬은 "negative 는 catalog 의 권장값을 그대로 쓰고, 추가
속성-보호 negative 만 덧붙여라" 로 안내 가능.

**필요한 서버 변경**:
- `to_catalog()` 가 `defaults.negative_prompt` preset reference 를 resolve 한 결과를 노출.
- 또는 `recommended_negative_preset_name` 필드 추가 (`NEG_PIXEL_SPRITE`).

---

### 5. `/api/workflows/generate` 응답에 결과 asset_id / 폴링 토큰 즉시 포함

**갭**:
스킬이 `--wait` 를 권장하지만, 비동기 폴링 시 어떤 ID 로 상태를 묻는지 / 결과 asset 들을
어떻게 매핑하는지 코드 안 본 사람은 모른다 (현재 readme/handoff 에 단편적으로만 있음).

**스킬이 가정하는 인터페이스**:
```jsonc
// POST /api/workflows/generate 즉시 응답 (--wait 없이도 동일)
{
  "run_id": "run_01H...",
  "status": "queued",
  "expected_outputs": [
    {"label": "stage1",     "asset_id": null},
    {"label": "pixelized",  "asset_id": null},
    {"label": "pixel_alpha","asset_id": null}  // primary
  ],
  "poll_url": "/api/runs/run_01H.../status",
  "estimated_duration_sec": 35
}
```

폴링이 끝나면 같은 구조에서 `asset_id` 가 채워지고 `status: completed` 로.

**필요한 서버 변경**:
- 응답 스키마 정형화 (현재는 시점/필드 일관성 추적 필요).
- `expected_outputs` 는 변형의 `outputs` 배열 그대로.

---

### 6. `from-asset` 입력에서 catalog outputs label 자동 매칭

**갭**:
현재 `POST /api/workflows/inputs/from-asset` 은 asset_id 만 받는다. 한 run 의 결과가
multi-output 일 때 (sprite/pixel_alpha = 3장), 어느 출력 라벨 (`stage1` / `pixel_alpha`)
을 다음 단계 입력으로 쓸지는 호출자가 직접 asset_id 를 골라야 한다.

**스킬이 가정하는 인터페이스**:
```bash
# run_id + output_label 로 chain — asset_id 직접 다루지 않음
af workflow upload --from-run <run_id> --output pixel_alpha
# 또는 generate 에서 직접
af workflow gen ... --input source_image=run:<run_id>/pixel_alpha
```

**필요한 서버 변경**:
- `from-asset` 엔드포인트에 `run_id + output_label` 두 키 받기 (`asset_id` 와 OR).
- 서버가 run/output_label → asset_id 룩업.

---

### 7. Bypass 모드의 retention / GC 정책 노출

**갭**:
P0-1 의 bypass 모드를 도입하면 임시 자산이 빠르게 쌓인다. 스킬이 "bypass 는 N일 후 사라진다"
라고 가정하고 안내하려면 그 N 이 catalog/health 에 노출돼야 한다.

**스킬이 가정하는 인터페이스**:
`GET /api/health` 또는 `GET /api/config` 에:
```json
{ "bypass_retention_days": 7 }
```

**필요한 서버 변경**:
- 환경변수 `AF_BYPASS_RETENTION_DAYS` (default 7).
- cron job (현재 미구현 — TODOS.md 의 "GC cron" 항목과 합쳐도 됨).
- 값을 health/config API 에 노출.

---

## P2 — 운영 편의

### 8. catalog 에 `deprecated` 플래그 — A1111 legacy 변형 표시

**갭**:
A1111 직접 호출은 스킬에서 폐기됐지만 코드/문서에 잔존 경로가 있다 (`af catalog models` /
`af gen` / `af batch`). 스킬이 "쓰지 마라" 고만 하면 충분하지 않고, 서버가 응답에서
deprecated 신호를 줘야 미래에 다시 미끌어지지 않는다.

**스킬이 가정하는 인터페이스**:
```jsonc
// /api/sd/catalog/models (legacy)
{ "items": [...], "deprecated": true,
  "deprecation_message": "Use /api/workflows/catalog instead." }
```

**필요한 서버 변경**:
- A1111 대상 엔드포인트 응답 헤더 `Deprecation: true` + `Sunset: <date>`.
- 응답 body 에도 동일 메타.
- CLI `af catalog *` 호출 시 stderr 경고 1회.

---

### 9. Workflow dry-run — patch 결과만 반환하는 모드

**갭**:
스킬이 "이 워크플로우가 내 인자를 어떻게 받아들이는지" 사전 점검할 방법이 없다. 잘못된
load_images 라벨이나 매칭 실패는 실제 ComfyUI 호출 후에야 드러난다 (시간/GPU 낭비).

**스킬이 가정하는 인터페이스**:
```http
POST /api/workflows/generate?dry_run=true
→ 200 { "patch_report": {"applied": {...}, "skipped": [...]}, "would_run": true }
```

**필요한 서버 변경**:
- `dry_run=true` 쿼리 파라미터 → ComfyUI 호출은 하지 않고 `workflow_patcher.patch_workflow`
  의 `PatchReport` 를 그대로 반환.
- 인증/권한은 동일.

---

### 10. Variant aliases — 의도 기반 단축 이름

**갭**:
"게임용 캐릭터" → `sprite/pixel_alpha`, "마케팅 표지" → `illustration/animagine_hires`
같은 매핑은 스킬 마크다운 표에 있을 뿐 서버는 모른다. 매핑이 바뀌어도 스킬을 다시 써야 함.

**스킬이 가정하는 인터페이스**:
```bash
af workflow gen @character "..." --wait    # = sprite/pixel_alpha
af workflow gen @marketing "..." --wait    # = illustration/animagine_hires
```

**필요한 서버 변경**:
- `registry.yml` 에 카테고리/변형과 별개로 `aliases:` 섹션:
  ```yaml
  aliases:
    "@character":  { category: sprite, variant: pixel_alpha }
    "@marketing":  { category: illustration, variant: animagine_hires }
    "@sketch":     { category: sprite, variant: pixel_alpha, approval_mode: bypass }
  ```
- catalog 응답에 `aliases` 노출.

---

## 스킬이 가정하는 최종 호출 인터페이스 (요약)

```bash
# 1) 카탈로그 / 변형 메타
af workflow catalog                          # 카테고리 → 변형 트리 + input_labels + aliases
af workflow describe sprite/pixel_alpha      # 단일 변형 상세 (input_labels, defaults, outputs, recommended_negatives)

# 2) 동적 입력 업로드 — CLI 1-step
af workflow upload ./pose.png                                    # 로컬 파일
af workflow upload --from-run <run_id> --output pixel_alpha      # 이전 run 의 특정 출력
af workflow upload --from-asset <asset_id>                       # 임의 에셋

# 3) 생성 — 통합
af workflow gen sprite/pixel_alpha proj key "prompt" \
   --input source_image=@./pose.png \
   --input pose_image=run:<run_id>/pixel_alpha \
   --candidates 4 \
   --wait

# 4) 모드별
af workflow gen ... --bypass-approval                            # 임시/스캐치/시뮬
af workflow gen ... --dry-run                                    # patch 점검만

# 5) 결과 회수 (변경 없음)
af list <project> [--include-bypassed]
af get <asset_id> -o out.png
af export <project> --manifest                                    # bypass 자산은 제외

# 6) Aliases — 의도 기반 호출
af workflow gen @character proj key "..." --wait                  # sprite/pixel_alpha
af workflow gen @sketch proj key "..." --wait                     # bypass 자동 적용
```

---

## 완료 정의 (Definition of Done)

이 문서의 P0 항목 (1, 2, 3) 이 머지되면:
1. `~/workspace/sincerity-skills/asset-factory-api/SKILL.md` 의 새 버전을 별도 변경 없이
   release 가능.
2. asset-relay-agent 가 SD 직접 호출 코드 / 멘탈모델 없이 이 스킬 한 장으로 모든 에셋
   파이프라인을 실행.
3. `bypass-approval` 모드로 100장 시뮬을 돌려도 사람 승인 큐가 오염되지 않음.

P1, P2 는 반복적으로 채워가도 무방.

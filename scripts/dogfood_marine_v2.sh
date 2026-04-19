#!/usr/bin/env bash
# v0.1 dogfooding runbook: marine_v2_idle 100장 → 1장 cherry-pick 시간 측정.
#
# 목표: enqueue → 다음날 아침 cherry-pick → 1장 승인까지 사람 손이 닿는 시간 5분 이내.
# 사용:
#   AF_HOST=http://localhost:8000 \
#   API_KEY=secret \
#   ./scripts/dogfood_marine_v2.sh
#
# 측정 포인트(수동):
#   T0  = 스크립트 enqueue 응답을 받은 시각
#   T1  = 사람이 /cherry-pick?batch=<id> 를 연 시각
#   T2  = 사람이 cherry-pick을 끝내고 승인을 누른 시각
#   사람 손 시간 = T2 - T1   (목표: <= 5분)

set -euo pipefail

AF_HOST="${AF_HOST:-http://localhost:8000}"
PROJECT="${PROJECT:-cat-raising}"
ASSET_KEY="${ASSET_KEY:-marine_v2_idle}"

if [[ -n "${API_KEY:-}" ]]; then
  AUTH=(-H "x-api-key: $API_KEY")
else
  AUTH=()
fi

read -r -d '' SPEC <<'JSON' || true
{
  "project": "__PROJECT__",
  "asset_key": "__ASSET_KEY__",
  "category": "character",
  "prompts": [
    "pixel art, marine character idle pose, transparent background, clean lineart, full body"
  ],
  "models": [
    "pixelart_xl_v1.5",
    "deliberate_v3"
  ],
  "loras": [
    {"name": "marine_uniform_v2", "weight": 0.7},
    {"name": "sad_face_v1", "weight": 0.5}
  ],
  "seeds_per_combo": 25,
  "common": {
    "steps": 28,
    "cfg_scale": 7.0,
    "sampler_name": "DPM++ 2M Karras",
    "negative_prompt": "blurry, lowres, jpeg artifacts, watermark",
    "expected_size": 1024,
    "max_colors": 64
  }
}
JSON

SPEC="${SPEC//__PROJECT__/$PROJECT}"
SPEC="${SPEC//__ASSET_KEY__/$ASSET_KEY}"

echo "[dogfood] target = $AF_HOST"
echo "[dogfood] project=$PROJECT asset_key=$ASSET_KEY"
echo "[dogfood] T0 (enqueue start) = $(date -Iseconds)"

RESP=$(curl -sS --fail-with-body -X POST "$AF_HOST/api/mcp/design_asset" \
  -H "Content-Type: application/json" "${AUTH[@]}" \
  -d "$SPEC")

echo "[dogfood] response:"
echo "$RESP" | python3 -m json.tool

BATCH_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['structuredContent']['batch_id'])")
EXPANDED=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['structuredContent']['expanded_count'])")
ETA=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['structuredContent']['estimated_eta_seconds'])")

echo
echo "============================================================"
echo " batch_id        : $BATCH_ID"
echo " expanded_count  : $EXPANDED   (목표 100, 2 모델 x 2 LoRA x 25 seed = 100)"
echo " estimated_eta_s : $ETA        (~ task당 6초 추정)"
echo "------------------------------------------------------------"
echo " cherry-pick URL : $AF_HOST/cherry-pick?batch=$BATCH_ID"
echo "============================================================"
echo
echo "다음 단계:"
echo "  1) 위 URL을 즉시 북마크. (T1: 내일 아침 열 때 시각 기록)"
echo "  2) 아침에 화면 켜고 j/k 로 훑으면서 x 로 reject, Enter 로 approve."
echo "  3) 1장 approve 끝난 시각 (T2) 기록. T2-T1 이 5분 이하면 wedge 검증 PASS."

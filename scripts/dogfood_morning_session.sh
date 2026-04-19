#!/usr/bin/env bash
# v0.1.5 dogfooding: morning session 시뮬레이션 (5 batches × 평균 40장 ≈ 200장).
#
# wedge: "에이전트가 N×M×K 조합으로 밤새 enqueue → 다음 날 아침 사람이 cherry-pick UI에서
# 25-50분 안에 모든 batch 처리"가 성립하는지 본다. v0.1.5 헤더의 '오늘 큐: N batches (M장
# 남음)', batch spacer 자동 다음, undo toast 가 실제로 morning session 흐름에 잘 녹는지
# 손으로 측정한다.
#
# 사용:
#   AF_HOST=http://localhost:8000 API_KEY=secret ./scripts/dogfood_morning_session.sh
#
# 측정 포인트(수동, 시계 또는 stopwatch):
#   T0 = 모든 enqueue 완료한 시각 (스크립트 끝 시각)
#   T1 = /cherry-pick 페이지를 연 시각 (다음 날 아침)
#   T2 = morning summary("오늘 cherry-pick 완료") 화면이 뜬 시각
#   사람 손 시간 = T2 - T1 (목표: 25-50분, 200장 약 30분 권장)
#
# 보조 측정:
#   - rejected/approved 카운트 (summary 화면에 표시됨)
#   - undo toast를 실제로 한 번도 안 썼나 / 몇 번 썼나
#   - batch spacer 15초가 길지/짧지 체감
#
# 결과는 README의 "v0.1.5 morning session 측정" 섹션에 표로 누적합니다.

set -euo pipefail

AF_HOST="${AF_HOST:-http://localhost:8000}"
PROJECT="${PROJECT:-cat-raising}"

if [[ -n "${API_KEY:-}" ]]; then
  AUTH=(-H "x-api-key: $API_KEY")
else
  AUTH=()
fi

# (asset_key, prompt_keyword, seeds_per_combo) — 100장(이상적), 20장 fallback 혼합
declare -a SCENARIOS=(
  "marine_v2_idle|marine character idle pose|25"   # 2x2x25 = 100장
  "marine_v2_walk|marine character walking|10"     # 2x2x10 =  40장
  "soldier_idle|soldier idle pose|10"              # 2x2x10 =  40장
  "scout_run|scout running pose|5"                 # 2x2x5  =  20장 (fallback)
  "engineer_repair|engineer repairing|5"           # 2x2x5  =  20장 (fallback)
)

enqueue_one() {
  local asset_key="$1" prompt_kw="$2" seeds="$3"
  local spec
  spec=$(cat <<JSON
{
  "project": "$PROJECT",
  "asset_key": "$asset_key",
  "category": "character",
  "prompts": ["pixel art, $prompt_kw, transparent background, clean lineart, full body"],
  "models": ["pixelart_xl_v1.5", "deliberate_v3"],
  "loras": [
    {"name": "marine_uniform_v2", "weight": 0.7},
    {"name": "sad_face_v1", "weight": 0.5}
  ],
  "seeds_per_combo": $seeds,
  "common": {
    "steps": 28, "cfg_scale": 7.0, "sampler_name": "DPM++ 2M Karras",
    "negative_prompt": "blurry, lowres, jpeg artifacts, watermark",
    "expected_size": 1024, "max_colors": 64
  }
}
JSON
)
  local resp
  resp=$(curl -sS --fail-with-body -X POST "$AF_HOST/api/batches" \
    -H "Content-Type: application/json" "${AUTH[@]}" -d "$spec")
  echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"  batch_id={d['batch_id']} expanded={d['expanded_count']} eta~{d['estimated_eta_seconds']}s\")
"
}

echo "[morning-session] target = $AF_HOST"
echo "[morning-session] enqueue 시작 = $(date -Iseconds)"
echo
TOTAL=0
for row in "${SCENARIOS[@]}"; do
  IFS='|' read -r ak kw seeds <<<"$row"
  combos=$((2 * 2 * seeds))
  TOTAL=$((TOTAL + combos))
  echo "[morning-session] enqueue $ak ($combos장)"
  enqueue_one "$ak" "$kw" "$seeds"
done

echo
echo "[morning-session] enqueue 완료 = $(date -Iseconds)"
echo "============================================================"
echo " 총 $TOTAL 장 enqueue (5 batches)"
echo " cherry-pick URL : $AF_HOST/cherry-pick"
echo "============================================================"
echo
echo "다음 단계 (다음 날 아침):"
echo "  T1 = 위 URL을 연 시각 기록"
echo "  헤더 pill에 '오늘 큐: 5 batches ($TOTAL장 남음)' 가 뜨는지 확인"
echo "  j/k 로 훑고 enter 로 승인, x 로 reject. batch 끝나면 spacer 카드 → 자동 다음"
echo "  T2 = '오늘 cherry-pick 완료' 화면이 뜬 시각 기록"
echo "  목표: T2 - T1 <= 30분 (1장당 약 9초 페이스)"

"""dogfood 셸 스크립트 안에 박혀 있는 batch JSON 스펙이 현재
``DesignBatchRequest`` 스키마 (ComfyUI workflow 곱집합 — workflow_category /
workflow_variants / workflow_params_overrides) 와 호환되는지 검증한다.

회귀 방지: A1111 시절의 ``models`` / ``loras`` flat 페이로드가 스크립트에
남아 있으면 ``/api/batches`` 가 422로 실패하여 wedge 측정 자체가 안 된다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from server import DesignBatchRequest


SCRIPT_PATHS = [
    Path(__file__).resolve().parent.parent / "scripts" / "dogfood_marine_v2.sh",
    Path(__file__).resolve().parent.parent / "scripts" / "dogfood_morning_session.sh",
]


_HEREDOC_RE = re.compile(
    r"<<\s*['\"]?(?P<tag>[A-Za-z_][A-Za-z_0-9]*)['\"]?[^\n]*\n"
    r"(?P<body>.*?)\n(?P=tag)\b",
    re.DOTALL,
)


def _extract_first_json_block(text: str) -> str:
    """``<<JSON ... JSON`` heredoc 본문을 뽑는다.

    셸 파라미터 확장(``${AF_HOST:-...}``)도 ``{...}`` 형태라 토큰 균형 파서로
    스크립트 전체를 훑으면 잘못 잡힌다. heredoc 경계로 한정해서 안전하게 파싱.
    """
    for m in _HEREDOC_RE.finditer(text):
        body = m.group("body").strip()
        if body.startswith("{"):
            return body
    raise ValueError("no JSON-shaped heredoc body found")


def _normalize_for_validation(blob: str) -> str:
    # 1) "$var" 같이 이미 따옴표 안에 있는 셸 변수는 따옴표째로 더미 문자열로 치환.
    blob = re.sub(r'"\$[A-Za-z_][A-Za-z_0-9]*"', '"x"', blob)
    # 2) 따옴표 안의 __PLACEHOLDER__ 토큰도 마찬가지.
    blob = re.sub(r'"__[A-Z_]+__"', '"x"', blob)
    # 3) 그 밖의 bare 셸 변수는 숫자 컨텍스트일 가능성이 높으므로 1로 치환.
    blob = re.sub(r"\$[A-Za-z_][A-Za-z_0-9]*", "1", blob)
    blob = re.sub(r"__[A-Z_]+__", "x", blob)
    return blob


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=lambda p: p.name)
def test_dogfood_payload_matches_design_batch_request(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    blob = _extract_first_json_block(raw)
    blob = _normalize_for_validation(blob)
    payload = json.loads(blob)
    DesignBatchRequest.model_validate(payload)

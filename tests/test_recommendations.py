"""§1.C 변형 의도 기반 인덱스 / 자연어 검색 단위 테스트.

spec docs/NEXT.md §C.4 의 룰 5종 (intent 매칭 / use_cases / tags / not_for
페널티 / 빈 query) + recommend / search 조합 케이스.

미니 registry 를 사이드카 yaml 로 직접 작성 — `workflow_registry` 통과해
실제 매칭 검증.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recommendations import (
    Candidate,
    SearchMatch,
    _tokenize,
    recommend,
    search,
)
from workflow_registry import WorkflowRegistry


# ----------------------------------------------------------------------------
# helpers — 미니 registry + 사이드카 yaml
# ----------------------------------------------------------------------------


def _build_registry(tmp_path: Path) -> WorkflowRegistry:
    """3 변형 미니 레지스트리:
    - sprite/char_pixel: pixel-art character (intent 매칭 / tags / use_cases)
    - sprite/char_anime: anime character (다른 style — not_for 에 'scenery')
    - bg/scenery: 픽셀 배경 (캐릭터 not_for)
    """
    (tmp_path / "sprite").mkdir(parents=True)
    (tmp_path / "bg").mkdir(parents=True)
    wf = json.dumps({"3": {"class_type": "KSampler", "inputs": {"seed": 0}}})
    (tmp_path / "sprite" / "char_pixel.json").write_text(wf, encoding="utf-8")
    (tmp_path / "sprite" / "char_anime.json").write_text(wf, encoding="utf-8")
    (tmp_path / "bg" / "scenery.json").write_text(wf, encoding="utf-8")

    (tmp_path / "sprite" / "char_pixel.meta.yaml").write_text(
        """
schema_version: 1
intent: "Pixel-art character sprite for retro game"
use_cases:
  - "RPG character with pose sheet"
  - "Top-down 2D game sprite"
not_for:
  - "scenery / background — use bg/*"
tags:
  - pixel-art
  - character
  - sprite
  - rpg
""",
        encoding="utf-8",
    )
    (tmp_path / "sprite" / "char_anime.meta.yaml").write_text(
        """
schema_version: 1
intent: "Anime-style character illustration"
use_cases:
  - "Character portrait for marketing"
not_for:
  - "scenery — use bg/scenery"
tags:
  - anime
  - character
  - illustration
""",
        encoding="utf-8",
    )
    (tmp_path / "bg" / "scenery.meta.yaml").write_text(
        """
schema_version: 1
intent: "Pixel-art scenery / environment background"
use_cases:
  - "RPG fantasy environment"
not_for:
  - "character — use sprite/char_pixel"
tags:
  - pixel-art
  - scenery
  - background
  - environment
""",
        encoding="utf-8",
    )
    (tmp_path / "registry.yml").write_text(
        """
version: 1
categories:
  sprite:
    variants:
      char_pixel:
        file: sprite/char_pixel.json
        primary: true
        outputs: [{node_title: "Save", label: out, primary: true}]
      char_anime:
        file: sprite/char_anime.json
        outputs: [{node_title: "Save", label: out, primary: true}]
  bg:
    variants:
      scenery:
        file: bg/scenery.json
        outputs: [{node_title: "Save", label: out, primary: true}]
""",
        encoding="utf-8",
    )
    return WorkflowRegistry(root=tmp_path)


# ----------------------------------------------------------------------------
# _tokenize
# ----------------------------------------------------------------------------


def test_tokenize_lowercase_alnum() -> None:
    assert _tokenize("RPG Character") == ["rpg", "character"]


def test_tokenize_mixed_korean_english() -> None:
    tokens = _tokenize("RPG 캐릭터 픽셀 sprite")
    assert "rpg" in tokens
    assert "캐릭터" in tokens
    assert "픽셀" in tokens
    assert "sprite" in tokens


def test_tokenize_punctuation_stripped() -> None:
    assert _tokenize("hello, world! 1x3") == ["hello", "world", "1x3"]


def test_tokenize_empty_returns_empty_list() -> None:
    assert _tokenize("") == []
    assert _tokenize("   !!!   ") == []


# ----------------------------------------------------------------------------
# rule_score 5종 (spec §C.4)
# ----------------------------------------------------------------------------


def test_rule_score_intent_match(tmp_path: Path) -> None:
    """intent 매칭 → +0.4 (단순 substring)."""
    reg = _build_registry(tmp_path)
    # 'retro' query — char_pixel 의 intent ("Pixel-art character sprite for retro game") 만 매칭.
    # bg/scenery 는 not_for 가 'sprite/char_pixel' 를 가리키므로 query 'retro' 와 무관 (페널티 없음).
    result = recommend(reg, query="retro", top=5)
    by_var = {c.variant: c for c in result}
    # char_pixel: intent 매칭 (0.4), tag 'retro' 없음
    assert "sprite/char_pixel" in by_var
    assert by_var["sprite/char_pixel"].score == pytest.approx(0.4, abs=0.01)
    # char_anime / bg/scenery 의 intent 에 'retro' 없음 → 매칭 X
    assert "sprite/char_anime" not in by_var
    assert "bg/scenery" not in by_var


def test_rule_score_use_cases_match(tmp_path: Path) -> None:
    """use_cases 매칭 → +0.15 each, max +0.45."""
    reg = _build_registry(tmp_path)
    # 'rpg' query — char_pixel 의 use_case "RPG character with pose sheet" + tag 'rpg' 매칭
    result = recommend(reg, query="rpg", top=5)
    pix = next(c for c in result if c.variant == "sprite/char_pixel")
    # use_cases_hit 1개 + tag 1개 + intent 매칭 X (intent 에 'rpg' 없음)
    assert "RPG character with pose sheet" in pix.use_cases_hit
    assert "rpg" in pix.tags_hit
    # 0.15 (1 use_case) + 0.1 (1 tag) = 0.25
    assert pix.score == pytest.approx(0.25, abs=0.01)


def test_rule_score_tags_match(tmp_path: Path) -> None:
    """tags 매칭 → +0.1 each, max +0.3."""
    reg = _build_registry(tmp_path)
    # 'character' query — 두 sprite 의 tag 'character' 매칭 (intent 에도 'character' 있음)
    result = recommend(reg, query="character", top=5)
    by_var = {c.variant: c for c in result}
    # char_pixel: intent 매칭 (0.4) + tag 'character' (0.1) = 0.5
    assert "character" in by_var["sprite/char_pixel"].tags_hit
    assert by_var["sprite/char_pixel"].score >= 0.4


def test_rule_score_not_for_penalty(tmp_path: Path) -> None:
    """not_for 매칭 → -0.5 페널티 (오선택 차단)."""
    reg = _build_registry(tmp_path)
    # 'scenery' query — bg/scenery 의 intent + tag 매칭 (긍정), 두 sprite 의 not_for 매칭 (페널티)
    result = recommend(reg, query="scenery", top=5)
    by_var = {c.variant: c for c in result}
    # bg/scenery 점수 높음 (intent 매칭 0.4 + tag 'scenery' 0.1 = 0.5)
    assert "bg/scenery" in by_var
    assert by_var["bg/scenery"].score >= 0.4
    # sprite 변형들은 not_for 페널티로 score≤0 이라 결과에서 제외 (recommend 가 score=0 제외)
    assert "sprite/char_pixel" not in by_var
    assert "sprite/char_anime" not in by_var


def test_rule_score_empty_query_returns_empty(tmp_path: Path) -> None:
    """빈 query → 빈 리스트."""
    reg = _build_registry(tmp_path)
    assert recommend(reg, query="", top=5) == []
    assert recommend(reg, query="   ", top=5) == []
    # 토큰 안 나오는 query (특수문자만) 도 빈 리스트
    assert recommend(reg, query="!!!", top=5) == []


# ----------------------------------------------------------------------------
# recommend — 정렬 / top / available
# ----------------------------------------------------------------------------


def test_recommend_sorted_by_score_desc(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    # 'pixel art character' query — char_pixel 이 가장 매칭, bg/scenery 도 'pixel' 매칭
    result = recommend(reg, query="pixel art character rpg", top=5)
    assert len(result) >= 2
    scores = [c.score for c in result]
    assert scores == sorted(scores, reverse=True)


def test_recommend_top_truncates(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    result = recommend(reg, query="pixel character", top=1)
    assert len(result) == 1


def test_recommend_excludes_score_zero(tmp_path: Path) -> None:
    """매칭 안 된 변형은 결과에서 제외."""
    reg = _build_registry(tmp_path)
    result = recommend(reg, query="completely-unrelated-xyz123", top=10)
    assert result == []


def test_recommend_includes_unavailable_flag(tmp_path: Path) -> None:
    """include_unavailable=True 면 사이드카 yaml 있는 미가용 변형도 포함.

    (현재 fixture 는 모두 available — flag 동작은 스모크 검증.)
    """
    reg = _build_registry(tmp_path)
    result = recommend(reg, query="pixel", top=5, include_unavailable=True)
    assert len(result) >= 1


def test_recommend_response_has_intent_and_hits(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    result = recommend(reg, query="rpg pose sheet", top=3)
    pix = next(c for c in result if c.variant == "sprite/char_pixel")
    assert pix.intent == "Pixel-art character sprite for retro game"
    assert "RPG character with pose sheet" in pix.use_cases_hit


def test_candidate_to_dict_shape(tmp_path: Path) -> None:
    """Candidate.to_dict — 응답 직렬화."""
    reg = _build_registry(tmp_path)
    result = recommend(reg, query="rpg", top=1)
    d = result[0].to_dict()
    assert set(d.keys()) == {
        "variant", "score", "intent", "use_cases_hit", "tags_hit", "not_for_warnings"
    }
    assert isinstance(d["score"], float)


# ----------------------------------------------------------------------------
# search — tag 매칭 (정확)
# ----------------------------------------------------------------------------


def test_search_must_tags_intersect(tmp_path: Path) -> None:
    """모든 must_tags 가 변형의 tags 에 있어야 매칭."""
    reg = _build_registry(tmp_path)
    matches = search(reg, must_tags=["pixel-art", "character"])
    # char_pixel 만 두 tag 다 가짐 (bg/scenery 는 character 없음)
    names = {m.variant for m in matches}
    assert names == {"sprite/char_pixel"}


def test_search_must_not_excludes(tmp_path: Path) -> None:
    """must_not_tags 어느 하나라도 가지면 제외."""
    reg = _build_registry(tmp_path)
    # pixel-art 가지면서 scenery 없는 변형
    matches = search(reg, must_tags=["pixel-art"], must_not_tags=["scenery"])
    names = {m.variant for m in matches}
    assert names == {"sprite/char_pixel"}


def test_search_no_filter_returns_all_available(tmp_path: Path) -> None:
    """필터 미지정 → 모든 가용 변형."""
    reg = _build_registry(tmp_path)
    matches = search(reg)
    names = {m.variant for m in matches}
    assert names == {"sprite/char_pixel", "sprite/char_anime", "bg/scenery"}


def test_search_must_tag_no_match(tmp_path: Path) -> None:
    """존재하지 않는 tag 요청 → 빈 결과."""
    reg = _build_registry(tmp_path)
    assert search(reg, must_tags=["nonexistent-tag"]) == []


def test_search_case_insensitive(tmp_path: Path) -> None:
    """tag 매칭 case-insensitive."""
    reg = _build_registry(tmp_path)
    matches = search(reg, must_tags=["Pixel-Art"])
    names = {m.variant for m in matches}
    assert "sprite/char_pixel" in names
    assert "bg/scenery" in names


def test_search_match_to_dict(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    matches = search(reg, must_tags=["pixel-art"])
    d = matches[0].to_dict()
    assert set(d.keys()) == {"variant", "intent", "tags_hit"}

"""§1.C 변형 의도 기반 인덱스 / 자연어 검색 (`docs/NEXT.md §1.C`).

§A 의 `meta.intent` / `use_cases` / `not_for` / `tags` 를 인덱싱해 자연어 의도
→ ranked 변형 매칭 (Phase 1 룰 기반). Phase 2 임베딩은 변형 30+ 시 별도 PR.

두 인터페이스:
- `recommend(query, top, include_unavailable)`: 자연어 query → 점수 매긴 후보
- `search(must_tags, must_not_tags)`: 태그 색인 (정확 매칭)

스코어링 (spec §C.4 룰 기반):
- intent 매칭 (any token 포함) → +0.4
- use_cases 매칭마다 +0.15, max +0.45
- tags 매칭마다 +0.1, max +0.3
- not_for 매칭마다 -0.5 (페널티 강함 — 오선택 차단)
- 0.0–1.0 클램프
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from workflow_registry import VariantSpec, WorkflowRegistry


# 한국어 / 영어 / 숫자 토큰만. 점수 계산 시 query 와 meta 양쪽에 적용.
_TOKEN_RE = re.compile(r"[a-z0-9가-힣]+")


def _tokenize(text: str) -> list[str]:
    """text 를 lowercase 토큰 리스트로. 짧은 (1자) 토큰도 포함 — 사용자가 의도적으로
    써넣을 수 있음 (예: '3D', 'NPC')."""
    return _TOKEN_RE.findall(text.lower())


@dataclass(slots=True, frozen=True)
class Candidate:
    """recommend 응답의 단일 후보 (spec §C.3 candidates[]).

    변형 자체에 대한 메타 + query 매칭 디버깅 정보 (어떤 use_case / tag 가
    히트했는지). 점수만 보지 말고 ``not_for_warnings`` 도 클라이언트가 표시해야
    오선택 차단 (예: "scenery" query 인데 캐릭터 변형이 점수 높음 → not_for 페널티
    있어도 결과에 노출, 클라이언트가 경고 같이 표시).
    """

    variant: str        # "<category>/<name>"
    score: float        # 0.0–1.0
    intent: str
    use_cases_hit: tuple[str, ...]
    tags_hit: tuple[str, ...]
    not_for_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "score": round(self.score, 4),
            "intent": self.intent,
            "use_cases_hit": list(self.use_cases_hit),
            "tags_hit": list(self.tags_hit),
            "not_for_warnings": list(self.not_for_warnings),
        }


@dataclass(slots=True, frozen=True)
class SearchMatch:
    """search 응답의 단일 매치 (spec §C.3 GET /search 의 matches[])."""

    variant: str
    intent: str
    tags_hit: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "intent": self.intent,
            "tags_hit": list(self.tags_hit),
        }


def _score_one(
    q_tokens: set[str], variant: VariantSpec
) -> tuple[float, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """단일 변형의 score + hits. 메타 부재 변형은 모두 0 / 빈 hits."""
    meta = variant.meta
    score = 0.0

    intent_lower = meta.intent.lower()
    intent_hit = any(t in intent_lower for t in q_tokens) if q_tokens else False
    if intent_hit:
        score += 0.4

    # use_cases — 매칭된 항목 그대로 노출 (오선택 디버깅용).
    use_cases_hit: list[str] = []
    for uc in meta.use_cases:
        uc_low = uc.lower()
        if any(t in uc_low for t in q_tokens):
            use_cases_hit.append(uc)
    score += min(len(use_cases_hit) * 0.15, 0.45)

    # tags — exact token 매칭 (substring 아님). 'pixel-art' 가 하나의 태그 토큰이라
    # query 가 'pixel' 만 보내면 매칭 X. 사용자가 정확한 태그 단어를 넣어야 함.
    # 유저친화적 매칭: tag 이름의 단어 분할도 매칭 — 'pixel-art' 는 'pixel' / 'art' 로 분할.
    tags_hit: list[str] = []
    for tag in meta.tags:
        tag_tokens = set(_tokenize(tag))
        if tag_tokens & q_tokens:   # 교집합 있으면 매칭
            tags_hit.append(tag)
    score += min(len(tags_hit) * 0.1, 0.3)

    # not_for 페널티 — 매칭 시 -0.5 (강한 페널티, 오선택 차단).
    not_for_hits: list[str] = []
    for nf in meta.not_for:
        nf_low = nf.lower()
        if any(t in nf_low for t in q_tokens):
            not_for_hits.append(nf)
    score -= len(not_for_hits) * 0.5

    score = max(0.0, min(1.0, score))
    return score, tuple(use_cases_hit), tuple(tags_hit), tuple(not_for_hits)


def recommend(
    registry: WorkflowRegistry,
    *,
    query: str,
    top: int = 3,
    include_unavailable: bool = False,
) -> list[Candidate]:
    """자연어 query 에 대해 점수 매긴 후보 top-N.

    - 빈 query: 빈 리스트 반환 (의미 없음).
    - score=0 후보는 제외 (정렬 후).
    - top: 1~50 범위로 클램프.
    """
    query = query.strip()
    if not query:
        return []
    top = max(1, min(50, int(top)))

    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return []

    candidates: list[Candidate] = []
    for cat in registry.categories.values():
        for v in cat.variants.values():
            if not include_unavailable and not v.available:
                continue
            score, use_cases_hit, tags_hit, not_for_warnings = _score_one(q_tokens, v)
            if score <= 0.0:
                continue
            candidates.append(Candidate(
                variant=f"{v.category}/{v.name}",
                score=score,
                intent=v.meta.intent,
                use_cases_hit=use_cases_hit,
                tags_hit=tags_hit,
                not_for_warnings=not_for_warnings,
            ))

    # score 내림차순, tied 시 variant 알파벳.
    candidates.sort(key=lambda c: (-c.score, c.variant))
    return candidates[:top]


def search(
    registry: WorkflowRegistry,
    *,
    must_tags: list[str] | None = None,
    must_not_tags: list[str] | None = None,
    include_unavailable: bool = False,
) -> list[SearchMatch]:
    """tag 색인 검색 — 모든 ``must_tags`` 포함 + 어느 ``must_not_tags`` 도 미포함.

    tag 매칭은 case-insensitive exact (whole-tag). 부분 단어 매칭 X — query 와
    저장된 tag 가 정확히 같아야 함 (e.g. ``pixel-art`` ↔ ``pixel-art``).
    """
    must = {t.lower() for t in (must_tags or []) if t}
    must_not = {t.lower() for t in (must_not_tags or []) if t}

    matches: list[SearchMatch] = []
    for cat in registry.categories.values():
        for v in cat.variants.values():
            if not include_unavailable and not v.available:
                continue
            tag_set = {tag.lower() for tag in v.meta.tags}
            if must and not must.issubset(tag_set):
                continue
            if must_not and (must_not & tag_set):
                continue
            # 미지정 (둘 다 빈) 이면 모든 변형 반환 — 카탈로그처럼.
            tags_hit = tuple(sorted(tag_set & must)) if must else tuple(v.meta.tags)
            matches.append(SearchMatch(
                variant=f"{v.category}/{v.name}",
                intent=v.meta.intent,
                tags_hit=tags_hit,
            ))

    matches.sort(key=lambda m: m.variant)
    return matches

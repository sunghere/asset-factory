"""ComfyUI 워크플로우 레지스트리.

`workflows/registry.yml` 매니페스트를 로드해 카테고리·변형·파일 경로·출력
노드 메타·기본 파라미터를 dataclass 로 노출한다.

주요 사용처:
- ComfyUI 백엔드가 task 의 `category` + `variant` 로 워크플로우 JSON 을 찾을 때
- API 가 `/api/workflows/catalog` 로 사용자에게 변형 목록을 노출할 때
- patcher 가 출력 노드 라벨 ↔ 노드 ID 매핑이 필요할 때

설계:
- `class_type` 기반 노드 검색은 `workflow_patcher` 에서 처리 (여기 책임 아님)
- 매니페스트의 `${preset:NAME}` 토큰은 로드 시점에 치환된다
- 파일이 없거나 `status: needs_api_conversion` 인 변형은 `available=False` 로 표시
- 매니페스트 형식 위반은 즉시 `WorkflowRegistryError` 로 실패 (빠른 피드백)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml


_PRESET_RE = re.compile(r"\$\{preset:([A-Z0-9_]+)\}")


class WorkflowRegistryError(RuntimeError):
    """레지스트리 매니페스트 검증/로딩 실패."""


def infer_input_labels(workflow_json: dict[str, Any]) -> tuple["InputLabelSpec", ...]:
    """워크플로우 API JSON 을 스캔해 등록된 LoadImage 라벨을 자동 추출.

    각 LoadImage 노드의 `_meta.title` 을 patcher 의 `_LOAD_IMAGE_RULES` 와 대조해
    매칭되는 라벨을 InputLabelSpec 으로 반환. node.inputs.image 를 default 로
    채운다. 라벨 중복 시 첫 매칭만 사용 (등록 라벨은 상호 배타적).

    YAML 의 input_labels override 와 합쳐지기 전 베이스로 쓰인다.
    """
    # 지연 import — 순환 import 가능성 차단 + 임포트 실패해도 registry 자체는 로드됨
    from workflow_patcher import find_load_image_label

    seen: dict[str, InputLabelSpec] = {}
    for node in workflow_json.values():
        if not isinstance(node, dict):
            continue
        label = find_load_image_label(node)
        if label is None or label in seen:
            continue
        default = node.get("inputs", {}).get("image")
        seen[label] = InputLabelSpec(
            label=label,
            required=False,
            default=str(default) if default is not None else None,
            description="",
        )
    return tuple(seen.values())


@dataclass(slots=True, frozen=True)
class OutputSpec:
    """워크플로우의 한 SaveImage 출력 노드 메타.

    `node_title` 은 ComfyUI 워크플로우 JSON 의 ``_meta.title`` 과 정확 일치해야
    한다 (patcher 가 이걸로 노드 ID 를 찾는다)."""

    node_title: str
    label: str
    primary: bool = False


@dataclass(slots=True, frozen=True)
class InputLabelSpec:
    """변형이 `load_images` dict 로 받을 수 있는 LoadImage 라벨.

    워크플로우 JSON 의 LoadImage 노드를 `workflow_patcher._LOAD_IMAGE_RULES` 와
    매칭해 자동 추론한다. YAML `input_labels:` 섹션이 있으면 description /
    required / default 를 라벨 단위로 override.
    """

    label: str
    required: bool = False
    default: str | None = None
    description: str = ""


@dataclass(slots=True, frozen=True)
class VariantSpec:
    """한 카테고리 안의 변형 (= 호출 단위)."""

    category: str
    name: str
    description: str
    file: Path | None              # API JSON 절대 경로 (없으면 호출 불가)
    ui_file: Path | None            # UI JSON 경로 (있으면 변환 안내용)
    status: str                     # "ready" | "needs_api_conversion"
    primary: bool                   # 카테고리 대표 변형 여부
    outputs: tuple[OutputSpec, ...]
    defaults: dict[str, Any]
    input_labels: tuple[InputLabelSpec, ...] = ()

    @property
    def available(self) -> bool:
        """호출 가능 여부. file 경로가 실제로 존재해야 True."""
        return self.status == "ready" and self.file is not None and self.file.exists()

    @property
    def primary_output(self) -> OutputSpec | None:
        """대표 출력 노드 (없으면 None — 호출 후 모든 출력을 후보로 본다)."""
        for out in self.outputs:
            if out.primary:
                return out
        return self.outputs[0] if self.outputs else None

    def load_api_json(self) -> dict[str, Any]:
        """워크플로우 API JSON 을 dict 로 로드. 파일 없으면 즉시 예외."""
        if self.file is None or not self.file.exists():
            raise WorkflowRegistryError(
                f"variant {self.category}/{self.name} 은 호출 불가 (status={self.status},"
                f" file={self.file})"
            )
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowRegistryError(
                f"workflow JSON 로드 실패 {self.file}: {exc}"
            ) from exc


@dataclass(slots=True, frozen=True)
class CategorySpec:
    """카테고리 (sprite / illustration / pixel_bg / icon)."""

    name: str
    description: str
    variants: dict[str, VariantSpec] = field(default_factory=dict)

    @property
    def primary_variant(self) -> VariantSpec | None:
        """카테고리 대표 변형 (`primary: true` 또는 첫 번째 available 변형)."""
        for v in self.variants.values():
            if v.primary and v.available:
                return v
        for v in self.variants.values():
            if v.available:
                return v
        return None


class WorkflowRegistry:
    """`workflows/registry.yml` 을 로드한 결과 핸들."""

    def __init__(self, root: Path, manifest_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.manifest_path = (manifest_path or (root / "registry.yml")).resolve()
        self._raw: dict[str, Any] = {}
        self._presets: dict[str, str] = {}
        self._categories: dict[str, CategorySpec] = {}
        self._load()

    # ------------- 공개 API -------------------------------------------------

    @cached_property
    def categories(self) -> dict[str, CategorySpec]:
        return dict(self._categories)

    def category(self, name: str) -> CategorySpec:
        try:
            return self._categories[name]
        except KeyError as exc:
            raise WorkflowRegistryError(f"unknown category: {name}") from exc

    def variant(self, category: str, name: str) -> VariantSpec:
        cat = self.category(category)
        try:
            return cat.variants[name]
        except KeyError as exc:
            raise WorkflowRegistryError(
                f"unknown variant: {category}/{name} (available: "
                f"{sorted(cat.variants.keys())})"
            ) from exc

    def available_variants(self) -> list[VariantSpec]:
        """호출 가능한(=API JSON 있는) 변형만 평탄화해서 반환."""
        return [
            v
            for cat in self._categories.values()
            for v in cat.variants.values()
            if v.available
        ]

    def variants_needing_conversion(self) -> list[VariantSpec]:
        """UI 포맷만 있고 API 변환 필요한 변형 목록 (운영용 진단)."""
        return [
            v
            for cat in self._categories.values()
            for v in cat.variants.values()
            if v.status == "needs_api_conversion"
        ]

    def to_catalog(self) -> dict[str, Any]:
        """REST 응답에 그대로 쓸 수 있는 카탈로그 dict.

        P1.f — 호스트 fs 구조 노출 회피. ``file`` 은 ``self.root`` 기준 상대경로
        만 노출 (예: ``sprite/PoseExtract_V37_api.json``). resolve 결과가 root
        밖이면 (이론상 없지만 안전망) ``has_file: True`` 만 표시하고 경로는 숨김.
        """
        def _relative_file(p: Path | None) -> str | None:
            if p is None:
                return None
            try:
                return p.relative_to(self.root).as_posix()
            except ValueError:
                return None  # root 밖 — 경로 노출 안 함

        return {
            "version": int(self._raw.get("version", 1)),
            "categories": {
                cname: {
                    "description": cat.description,
                    "primary_variant": (
                        cat.primary_variant.name if cat.primary_variant else None
                    ),
                    "variants": {
                        vname: {
                            "description": v.description,
                            "available": v.available,
                            "status": v.status,
                            "primary": v.primary,
                            "file": _relative_file(v.file),
                            "has_file": v.file is not None,
                            "outputs": [
                                {"label": o.label, "primary": o.primary}
                                for o in v.outputs
                            ],
                            "defaults": v.defaults,
                            "input_labels": [
                                {
                                    "label": il.label,
                                    "required": il.required,
                                    "default": il.default,
                                    "description": il.description,
                                }
                                for il in v.input_labels
                            ],
                        }
                        for vname, v in cat.variants.items()
                    },
                }
                for cname, cat in self._categories.items()
            },
        }

    # ------------- 내부 로딩 -------------------------------------------------

    def _load(self) -> None:
        if not self.manifest_path.exists():
            raise WorkflowRegistryError(
                f"registry manifest not found: {self.manifest_path}"
            )
        try:
            self._raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise WorkflowRegistryError(f"manifest YAML parse error: {exc}") from exc

        self._presets = {
            str(k): str(v) for k, v in (self._raw.get("presets") or {}).items()
        }

        raw_cats = self._raw.get("categories") or {}
        if not isinstance(raw_cats, dict):
            raise WorkflowRegistryError("`categories` must be a mapping")

        for cname, cdata in raw_cats.items():
            if not isinstance(cdata, dict):
                raise WorkflowRegistryError(f"category `{cname}` must be a mapping")
            variants_raw = cdata.get("variants") or {}
            if not isinstance(variants_raw, dict):
                raise WorkflowRegistryError(
                    f"category `{cname}` variants must be a mapping"
                )

            variants: dict[str, VariantSpec] = {}
            for vname, vdata in variants_raw.items():
                variants[vname] = self._parse_variant(cname, vname, vdata or {})

            self._categories[str(cname)] = CategorySpec(
                name=str(cname),
                description=str(cdata.get("description") or "").strip(),
                variants=variants,
            )

    def _parse_variant(self, category: str, name: str, data: dict[str, Any]) -> VariantSpec:
        if not isinstance(data, dict):
            raise WorkflowRegistryError(
                f"variant {category}/{name} must be a mapping (got {type(data).__name__})"
            )

        file_raw = data.get("file")
        ui_file_raw = data.get("ui_file")
        status = str(data.get("status") or "ready")
        if status not in {"ready", "needs_api_conversion"}:
            raise WorkflowRegistryError(
                f"variant {category}/{name} has invalid status: {status}"
            )
        if status == "ready" and not file_raw:
            raise WorkflowRegistryError(
                f"variant {category}/{name} status=ready 인데 file 미지정"
            )

        file_path = (self.root / str(file_raw)).resolve() if file_raw else None
        ui_path = (self.root / str(ui_file_raw)).resolve() if ui_file_raw else None

        outputs_raw = data.get("outputs") or []
        if not isinstance(outputs_raw, list):
            raise WorkflowRegistryError(
                f"variant {category}/{name} outputs must be a list"
            )
        outputs = tuple(self._parse_output(category, name, o) for o in outputs_raw)

        defaults_raw = data.get("defaults") or {}
        if not isinstance(defaults_raw, dict):
            raise WorkflowRegistryError(
                f"variant {category}/{name} defaults must be a mapping"
            )
        defaults = {k: self._resolve_presets(v) for k, v in defaults_raw.items()}

        input_labels = self._build_input_labels(category, name, data, file_path, status)

        return VariantSpec(
            category=category,
            name=str(name),
            description=str(data.get("description") or "").strip(),
            file=file_path,
            ui_file=ui_path,
            status=status,
            primary=bool(data.get("primary", False)),
            outputs=outputs,
            defaults=defaults,
            input_labels=input_labels,
        )

    def _build_input_labels(
        self,
        category: str,
        name: str,
        data: dict[str, Any],
        file_path: Path | None,
        status: str,
    ) -> tuple[InputLabelSpec, ...]:
        """워크플로우 JSON 자동 추론 + YAML override 머지.

        1. file 이 호출 가능하면 JSON 로드해 베이스 라벨 추론.
        2. YAML 의 input_labels: 섹션이 있으면 같은 라벨에 description/required/
           default 덮어씀.
        3. YAML 에 추론에 없는 라벨이 있으면 startup 에러 (오타 방지).
        """
        # 베이스: 워크플로우 JSON 스캔으로 자동 추론.
        # 깨진 JSON 은 여기서 raise 하지 않는다 — load_api_json() 이 호출 시점에
        # 더 의미있는 에러를 낸다 (registry 로드는 best-effort 로 진행).
        inferred: dict[str, InputLabelSpec] = {}
        if status == "ready" and file_path is not None and file_path.exists():
            try:
                wf = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                wf = None
            if isinstance(wf, dict):
                for spec in infer_input_labels(wf):
                    inferred[spec.label] = spec

        # YAML override
        raw = data.get("input_labels")
        if raw is None:
            return tuple(inferred.values())
        if not isinstance(raw, list):
            raise WorkflowRegistryError(
                f"variant {category}/{name} input_labels must be a list"
            )

        merged: dict[str, InputLabelSpec] = dict(inferred)
        for item in raw:
            if not isinstance(item, dict):
                raise WorkflowRegistryError(
                    f"variant {category}/{name} input_labels entry must be a mapping"
                )
            label = item.get("label")
            if not label or not isinstance(label, str):
                raise WorkflowRegistryError(
                    f"variant {category}/{name} input_labels entry needs `label`"
                )
            if label not in inferred:
                raise WorkflowRegistryError(
                    f"variant {category}/{name} input_labels references unknown label "
                    f"`{label}` (워크플로우 JSON 의 LoadImage 노드에서 추론되지 않음. "
                    f"가능: {sorted(inferred.keys()) or 'none'})"
                )
            base = inferred[label]
            merged[label] = InputLabelSpec(
                label=label,
                required=bool(item.get("required", base.required)),
                default=(
                    str(item["default"]) if item.get("default") is not None
                    else base.default
                ),
                description=str(item.get("description", base.description)),
            )
        return tuple(merged.values())

    @staticmethod
    def _parse_output(category: str, variant: str, raw: Any) -> OutputSpec:
        if not isinstance(raw, dict):
            raise WorkflowRegistryError(
                f"variant {category}/{variant} output entry must be a mapping"
            )
        title = raw.get("node_title")
        label = raw.get("label")
        if not title or not label:
            raise WorkflowRegistryError(
                f"variant {category}/{variant} output entry needs both"
                f" `node_title` and `label`"
            )
        return OutputSpec(
            node_title=str(title),
            label=str(label),
            primary=bool(raw.get("primary", False)),
        )

    def _resolve_presets(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in self._presets:
                raise WorkflowRegistryError(f"unknown preset: {key}")
            return self._presets[key]

        return _PRESET_RE.sub(repl, value)


# ------------------------------------------------------------------ 싱글톤 헬퍼

_default_registry: WorkflowRegistry | None = None


def get_default_registry(root: Path | None = None) -> WorkflowRegistry:
    """asset-factory 기본 위치(`<repo>/workflows/`) 의 레지스트리.

    테스트에서는 `WorkflowRegistry(root=...)` 로 직접 생성해 의존성 주입.
    """
    global _default_registry
    if _default_registry is None or root is not None:
        base = root or (Path(__file__).resolve().parent / "workflows")
        _default_registry = WorkflowRegistry(root=base)
    return _default_registry

"""Stable Diffusion 백엔드 추상화.

asset-factory 워커는 task 의 ``backend`` 컬럼을 보고 적절한 구현체로
디스패치한다. 두 구현:

- ``A1111Backend`` — 기존 ``SDClient.txt2img`` 래핑. 항상 출력 1개.
- ``ComfyUIBackend`` — ``WorkflowRegistry`` + ``patch_workflow`` + ``ComfyUIClient``
  결합. 워크플로우 변형에 따라 출력 N개 (예: V38 full = 5개).

설계 원칙:
1. **결과 모양 통일** — 두 백엔드 모두 ``GenerationOutcome`` 반환,
   ``outputs[0]`` 이 항상 cherry-pick UI 의 메인 이미지가 되도록 정렬.
2. **에러 코드 호환** — 두 백엔드 모두 실패 시 ``SDError`` 던짐
   (재시도 정책 ``NON_RETRYABLE_SD_CODES`` 그대로 재사용).
3. **task dict 입력** — DB row (``generation_tasks``) 가 그대로 들어와도
   동작하도록 dict 키 기반 (KeyError 시 명확한 메시지).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from generator import GenerationResult, SDClient, SDError
from generator_comfyui import ComfyUIClient, ComfyUIResult
from workflow_patcher import patch_workflow
from workflow_registry import VariantSpec, WorkflowRegistry, WorkflowRegistryError


# ----------------------------------------------------------------------------
# Result 모델
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class GeneratedOutput:
    """한 백엔드 호출의 한 출력 이미지.

    ``label`` 은 cherry-pick UI 가 인지하는 의미적 라벨:
    - A1111: 항상 ``"image"``
    - ComfyUI: 변형의 ``OutputSpec.label`` (예: ``pixel_alpha``, ``stage1``)
    """

    label: str
    image_bytes: bytes
    is_primary: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GenerationOutcome:
    """한 task 호출의 모든 출력 + 공통 메타.

    Invariants:
    - ``outputs`` 는 1개 이상
    - ``outputs[0]`` 의 ``is_primary == True`` (정렬 보장)
    """

    outputs: list[GeneratedOutput]
    seed: int | None
    model: str | None
    backend: str                # "a1111" | "comfyui"
    width: int | None
    height: int | None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # invariant 런타임 검증 — primary 접근 전에 실패하도록.
        if not self.outputs:
            raise ValueError("GenerationOutcome.outputs 는 1개 이상이어야 함")

    @property
    def primary(self) -> GeneratedOutput:
        return self.outputs[0]

    @property
    def extras(self) -> list[GeneratedOutput]:
        return self.outputs[1:]


# ----------------------------------------------------------------------------
# Protocol
# ----------------------------------------------------------------------------


class SDBackend(Protocol):
    """SD 백엔드 공통 인터페이스."""

    name: str

    async def health_check(self) -> dict[str, Any]:
        """백엔드 상태 정보. 구현체가 자체 정의."""

    async def generate(self, task: dict[str, Any]) -> GenerationOutcome:
        """task dict 1개를 처리해 GenerationOutcome 반환."""


# ----------------------------------------------------------------------------
# A1111
# ----------------------------------------------------------------------------


class A1111Backend:
    """AUTOMATIC1111 WebUI 백엔드 — 단일 txt2img 호출."""

    name = "a1111"

    def __init__(self, client: SDClient) -> None:
        self.client = client

    async def health_check(self) -> dict[str, Any]:
        info = await self.client.health_check()
        return {**info, "backend": self.name}

    async def generate(self, task: dict[str, Any]) -> GenerationOutcome:
        width, height = self.client.choose_native_resolution(
            model_name=task.get("model_name"),
            width=task.get("width"),
            height=task.get("height"),
        )
        gen: GenerationResult = await self.client.txt2img(
            prompt=task["prompt"],
            negative_prompt=task.get("negative_prompt"),
            model_name=task.get("model_name"),
            width=width,
            height=height,
            steps=int(task.get("steps", 20)),
            cfg_scale=float(task.get("cfg", 7.0)),
            sampler_name=task.get("sampler") or "DPM++ 2M",
            seed=task.get("seed"),
        )
        return GenerationOutcome(
            outputs=[
                GeneratedOutput(
                    label="image",
                    image_bytes=gen.image_bytes,
                    is_primary=True,
                    metadata={},
                )
            ],
            seed=gen.seed,
            model=gen.model,
            backend=self.name,
            width=gen.width,
            height=gen.height,
            raw={},
        )


# ----------------------------------------------------------------------------
# ComfyUI
# ----------------------------------------------------------------------------


class ComfyUIBackend:
    """ComfyUI 백엔드 — 레지스트리 변형을 patch 한 워크플로우 실행."""

    name = "comfyui"

    def __init__(self, client: ComfyUIClient, registry: WorkflowRegistry) -> None:
        self.client = client
        self.registry = registry

    async def health_check(self) -> dict[str, Any]:
        info = await self.client.health_check()
        return {
            **info,
            "backend": self.name,
            "workflows_available": len(self.registry.available_variants()),
            "workflows_needing_conversion": len(self.registry.variants_needing_conversion()),
        }

    async def generate(self, task: dict[str, Any]) -> GenerationOutcome:
        category = task.get("workflow_category")
        variant_name = task.get("workflow_variant")
        if not category or not variant_name:
            raise SDError(
                "comfyui task 에 workflow_category/workflow_variant 누락",
                code="sd_client_error",
            )

        try:
            variant = self.registry.variant(category, variant_name)
        except WorkflowRegistryError as exc:
            raise SDError(str(exc), code="sd_client_error") from exc

        if not variant.available:
            raise SDError(
                f"variant {category}/{variant_name} 호출 불가 (status={variant.status})",
                code="sd_client_error",
            )

        wf = variant.load_api_json()

        # 패치 인자 병합 — 우선순위: task 명시값 > workflow_params_json > variant.defaults
        wf_params: dict[str, Any] = {}
        wf_params_raw = task.get("workflow_params_json")
        if wf_params_raw:
            try:
                parsed = json.loads(wf_params_raw)
                if isinstance(parsed, dict):
                    wf_params = parsed
            except json.JSONDecodeError as exc:
                raise SDError(
                    f"workflow_params_json 파싱 실패: {exc}",
                    code="sd_client_error",
                ) from exc

        defaults = variant.defaults
        # 헬퍼: a 가 None 이면 b, b 도 None 이면 c
        def pick(*vals: Any) -> Any:
            for v in vals:
                if v is not None:
                    return v
            return None

        # seed=None 이면 워크플로우 원본 (often 0 or hardcoded) 이 그대로 박혀
        # 매번 같은 결과가 나옴. P2.4 — 명시 안 됐으면 64-bit unsigned random 자동 생성.
        effective_seed = task.get("seed")
        if effective_seed is None:
            effective_seed = secrets.randbelow(2**63)

        patch_kwargs: dict[str, Any] = {
            "prompt": task["prompt"],
            "negative_prompt": pick(
                task.get("negative_prompt"),
                wf_params.get("negative_prompt"),
                defaults.get("negative_prompt"),
            ),
            "seed": effective_seed,
            "steps": pick(task.get("steps"), wf_params.get("steps"), defaults.get("steps")),
            "cfg": pick(task.get("cfg"), wf_params.get("cfg"), defaults.get("cfg")),
            "sampler_name": pick(
                task.get("sampler"),
                wf_params.get("sampler"),
                defaults.get("sampler"),
            ),
            "scheduler": pick(wf_params.get("scheduler"), defaults.get("scheduler")),
            "pose_image": pick(wf_params.get("pose_image"), defaults.get("pose_image")),
            "controlnet_strength": pick(
                wf_params.get("controlnet_strength"),
                defaults.get("controlnet_strength"),
            ),
            "checkpoint": pick(
                task.get("model_name"),
                wf_params.get("checkpoint"),
                defaults.get("checkpoint"),
            ),
            "width": pick(task.get("width"), wf_params.get("width"), defaults.get("width")),
            "height": pick(task.get("height"), wf_params.get("height"), defaults.get("height")),
            "lora_strengths": wf_params.get("lora_strengths"),
            # Multi-stage 워크플로우의 stage 별 KSampler 덮어쓰기.
            # variant.defaults 에 ksampler_overrides 를 박아 두면 사용자 task 가
            # 명시 안 해도 자동 적용되고, wf_params 가 우선시된다.
            "ksampler_overrides": pick(
                wf_params.get("ksampler_overrides"),
                defaults.get("ksampler_overrides"),
            ),
        }

        patched, patch_report = patch_workflow(wf, **patch_kwargs)
        result: ComfyUIResult = await self.client.submit_and_wait(patched)

        outputs = self._map_outputs(variant, patched, result)
        if not outputs:
            # P1.e — registry 의 expected node_title 과 ComfyUI 가 실제 보낸
            # 노드/title 을 양쪽 다 노출해 사용자가 mismatch 즉시 발견.
            expected_titles = [o.node_title for o in variant.outputs]
            actual: list[str] = []
            for img in result.outputs:
                node = patched.get(img.node_id) or {}
                title = (
                    node.get("_meta", {}).get("title", "") if isinstance(node, dict) else ""
                )
                actual.append(f"{img.node_id}:{title or '(no-title)'}")
            raise SDError(
                "ComfyUI 응답에 registry expected SaveImage 매칭 없음. "
                f"expected_titles={expected_titles}, actual_nodes={actual}, "
                f"prompt_id={result.prompt_id}",
                code="sd_server_error",
            )

        return GenerationOutcome(
            outputs=outputs,
            seed=effective_seed,
            model=f"{category}/{variant_name}",
            backend=self.name,
            width=patch_kwargs.get("width"),
            height=patch_kwargs.get("height"),
            raw={
                "prompt_id": result.prompt_id,
                "patch_applied": patch_report.applied,
                "patch_skipped": patch_report.skipped,
            },
        )

    @staticmethod
    def _map_outputs(
        variant: VariantSpec,
        patched_wf: dict[str, Any],
        result: ComfyUIResult,
    ) -> list[GeneratedOutput]:
        """ComfyUI 결과의 node_id → variant.outputs 의 OutputSpec 매핑.

        매핑 규칙: 패치된 워크플로우 안에서 ``_meta.title`` == ``OutputSpec.node_title``
        인 노드를 찾고, 그 노드 ID 와 일치하는 result 이미지를 묶는다.

        - registry 가 인지하지 못하는 노드 (variant.outputs 에 없는 SaveImage) 는
          드롭한다 (실수로 추가된 임시 SaveImage 노출 방지).
        - primary 가 명시 안 된 변형은 첫 출력을 primary 로 승격.
        - 정렬: primary 먼저, 나머지는 variant.outputs 선언 순서.
        """
        title_to_spec: dict[str, Any] = {o.node_title: o for o in variant.outputs}

        # node_id → OutputSpec 매핑
        node_to_spec: dict[str, Any] = {}
        for nid, node in patched_wf.items():
            if not isinstance(node, dict):
                continue
            title = node.get("_meta", {}).get("title", "")
            spec = title_to_spec.get(title)
            if spec is not None:
                node_to_spec[str(nid)] = spec

        outputs: list[GeneratedOutput] = []
        for img in result.outputs:
            spec = node_to_spec.get(img.node_id)
            if spec is None:
                continue
            outputs.append(
                GeneratedOutput(
                    label=spec.label,
                    image_bytes=img.image_bytes,
                    is_primary=bool(spec.primary),
                    metadata={
                        "filename": img.filename,
                        "subfolder": img.subfolder,
                        "type": img.type,
                        "node_id": img.node_id,
                    },
                )
            )

        if not outputs:
            return outputs

        # primary 보장: variant.outputs 선언 순서의 첫 번째 라벨을 우선 promote.
        # 없으면 result 순서 첫 번째로 fallback.
        if not any(o.is_primary for o in outputs):
            preferred_label = variant.outputs[0].label if variant.outputs else None
            promoted = False
            if preferred_label is not None:
                for i, o in enumerate(outputs):
                    if o.label == preferred_label:
                        outputs[i] = replace(o, is_primary=True)
                        promoted = True
                        break
            if not promoted:
                outputs[0] = replace(outputs[0], is_primary=True)

        # 정렬: primary 가 [0], 나머지는 variant.outputs 선언 순서
        order = {o.label: i for i, o in enumerate(variant.outputs)}
        outputs.sort(key=lambda o: (not o.is_primary, order.get(o.label, 9999)))
        return outputs


# ----------------------------------------------------------------------------
# 디스패처
# ----------------------------------------------------------------------------


class BackendRegistry:
    """task 의 ``backend`` 컬럼 → 구현체 디스패치."""

    def __init__(self, backends: dict[str, SDBackend]) -> None:
        self._backends = dict(backends)

    def get(self, name: str | None) -> SDBackend:
        # 마이그레이션 호환: backend 컬럼이 NULL/공백/누락이면 a1111 로 가정.
        key = (name or "").strip() or "a1111"
        try:
            return self._backends[key]
        except KeyError as exc:
            raise SDError(f"unknown backend: {key}", code="sd_client_error") from exc

    @property
    def names(self) -> list[str]:
        return list(self._backends.keys())

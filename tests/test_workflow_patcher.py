"""workflow_patcher 단위 테스트.

class_type + title 검색 기반 패치, 매칭 노드 부재 시 안전 스킵, deep copy
원본 보존, lora_strengths 매칭, PoseExtract 같은 다른 LoadImage 가 있는
워크플로우의 안전성 검증.
"""

from __future__ import annotations

import copy
from typing import Any

from workflow_patcher import find_nodes, patch_workflow


# ----------------------------------------------------------------------------
# Fixtures — 인-메모리 워크플로우 (실 파일 의존 X)
# ----------------------------------------------------------------------------


def _v38_like() -> dict[str, dict[str, Any]]:
    """V38 sprite pixel_alpha 와 동등한 노드 구조."""
    return {
        "2": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "hyphoria_v002.safetensors"},
            "_meta": {"title": "CheckpointLoaderSimple"},
        },
        "3": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": "pixel_character_sprite_illustrious.safetensors",
                "strength_model": 0.7,
                "strength_clip": 0.7,
            },
            "_meta": {"title": "LoRA: pixel_character_sprite_illustrious"},
        },
        "4": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": "pixelart_up.safetensors",
                "strength_model": 0.4,
                "strength_clip": 0.4,
            },
            "_meta": {"title": "LoRA: pixelart_up"},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "ORIGINAL POSITIVE"},
            "_meta": {"title": "Positive Prompt"},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "ORIGINAL NEGATIVE"},
            "_meta": {"title": "Negative Prompt"},
        },
        "7": {
            "class_type": "LoadImage",
            "inputs": {"image": "pose_grid_default.png"},
            "_meta": {"title": "Pose grid (ControlNet input)"},
        },
        "8": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "control-lora-openposeXL2.safetensors"},
            "_meta": {"title": "ControlNet OpenPose"},
        },
        "9": {
            "class_type": "ControlNetApply",
            "inputs": {"strength": 0.85},
            "_meta": {"title": "Apply ControlNet"},
        },
        "10": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1280, "height": 640, "batch_size": 1},
            "_meta": {"title": "Latent (1280x640)"},
        },
        "11": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 999,
                "steps": 30,
                "cfg": 6.5,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1,
            },
            "_meta": {"title": "KSampler #1"},
        },
        "13": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "stage1"},
            "_meta": {"title": "Save Stage1"},
        },
    }


def _v36_pro_like() -> dict[str, dict[str, Any]]:
    """V36 Pro 처럼 ControlNet/LoadImage 없는 워크플로우."""
    return {
        "2": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "hyphoria_v002.safetensors"},
            "_meta": {"title": "CheckpointLoaderSimple"},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "OG"},
            "_meta": {"title": "Positive Prompt"},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "OG"},
            "_meta": {"title": "Negative Prompt"},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {"seed": 1, "steps": 30, "cfg": 6.5, "sampler_name": "dpmpp_2m", "scheduler": "karras"},
            "_meta": {"title": "KSampler #1"},
        },
    }


def _pose_extract_like() -> dict[str, dict[str, Any]]:
    """PoseExtract V37 — LoadImage title 이 'Pose grid' 가 아닌 'Load source image'."""
    return {
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": "your_image.png"},
            "_meta": {"title": "Load source image"},
        },
        "3": {
            "class_type": "OpenposePreprocessor",
            "inputs": {},
            "_meta": {"title": "OpenPose Preprocessor"},
        },
    }


# ----------------------------------------------------------------------------
# find_nodes
# ----------------------------------------------------------------------------


def test_find_nodes_by_class_type() -> None:
    wf = _v38_like()
    matches = find_nodes(wf, "LoraLoader")
    assert {nid for nid, _ in matches} == {"3", "4"}


def test_find_nodes_with_title_regex() -> None:
    wf = _v38_like()
    matches = find_nodes(wf, "CLIPTextEncode", title_match=r"^Positive Prompt$")
    assert [nid for nid, _ in matches] == ["5"]


def test_find_nodes_returns_empty_when_no_match() -> None:
    wf = _v36_pro_like()
    assert find_nodes(wf, "ControlNetApply") == []


def test_find_nodes_skips_non_dict_values() -> None:
    wf = {**_v36_pro_like(), "junk_key": "not a node"}
    # 안 죽고 결과만 반환
    matches = find_nodes(wf, "KSampler")
    assert len(matches) == 1


# ----------------------------------------------------------------------------
# patch_workflow — 기본 동작
# ----------------------------------------------------------------------------


def test_patch_basic_prompt_seed() -> None:
    wf = _v38_like()
    patched, report = patch_workflow(wf, prompt="NEW POSITIVE", seed=42)

    assert patched["5"]["inputs"]["text"] == "NEW POSITIVE"
    assert patched["11"]["inputs"]["seed"] == 42
    assert "prompt" in report.applied
    assert "seed" in report.applied
    assert report.applied["prompt"] == ["5"]
    assert report.applied["seed"] == ["11"]
    assert report.skipped == []


def test_patch_full_kwargs() -> None:
    wf = _v38_like()
    patched, report = patch_workflow(
        wf,
        prompt="P", negative_prompt="N",
        seed=1, steps=25, cfg=7.0,
        sampler_name="dpmpp_3m", scheduler="exponential",
        pose_image="other.png", controlnet_strength=0.9,
        checkpoint="other.safetensors",
        width=1024, height=768,
        lora_strengths={"pixel_character_sprite_illustrious": 0.85},
    )

    assert patched["2"]["inputs"]["ckpt_name"] == "other.safetensors"
    assert patched["3"]["inputs"]["strength_model"] == 0.85
    assert patched["3"]["inputs"]["strength_clip"] == 0.85
    assert patched["4"]["inputs"]["strength_model"] == 0.4   # 매핑 안 한 것은 그대로
    assert patched["5"]["inputs"]["text"] == "P"
    assert patched["6"]["inputs"]["text"] == "N"
    assert patched["7"]["inputs"]["image"] == "other.png"
    assert patched["9"]["inputs"]["strength"] == 0.9
    assert patched["10"]["inputs"]["width"] == 1024
    assert patched["10"]["inputs"]["height"] == 768
    assert patched["11"]["inputs"]["seed"] == 1
    assert patched["11"]["inputs"]["steps"] == 25
    assert patched["11"]["inputs"]["cfg"] == 7.0
    assert patched["11"]["inputs"]["sampler_name"] == "dpmpp_3m"
    assert patched["11"]["inputs"]["scheduler"] == "exponential"

    # 모두 매칭됐고 스킵 없음
    assert set(report.applied.keys()) >= {
        "prompt", "negative_prompt", "seed", "steps", "cfg",
        "sampler_name", "scheduler", "pose_image", "controlnet_strength",
        "checkpoint", "width", "height", "lora_strengths",
    }
    assert report.skipped == []


def test_patch_none_args_are_ignored() -> None:
    wf = _v38_like()
    original_seed = wf["11"]["inputs"]["seed"]
    patched, report = patch_workflow(wf, prompt="X", seed=None)
    assert patched["11"]["inputs"]["seed"] == original_seed
    assert "seed" not in report.applied
    assert "seed" not in report.skipped  # None 은 skipped 도 아니다 (단순 무시)


# ----------------------------------------------------------------------------
# 안전 스킵 — 매칭 노드 없을 때
# ----------------------------------------------------------------------------


def test_patch_safely_skips_missing_controlnet_in_v36_pro() -> None:
    wf = _v36_pro_like()
    patched, report = patch_workflow(
        wf,
        prompt="P", seed=99,
        pose_image="something.png",
        controlnet_strength=0.5,
    )
    assert patched["5"]["inputs"]["text"] == "P"
    assert patched["8"]["inputs"]["seed"] == 99
    assert "pose_image" in report.skipped
    assert "controlnet_strength" in report.skipped
    # applied 에는 prompt/seed 만
    assert "pose_image" not in report.applied
    assert "controlnet_strength" not in report.applied


def test_pose_extract_load_image_not_patched() -> None:
    """PoseExtract 의 LoadImage 는 title='Load source image' — 'Pose grid' 정규식 매칭 X.

    pose_image 패치가 의도치 않게 PoseExtract 의 입력 이미지를 덮어쓰면 안됨.
    """
    wf = _pose_extract_like()
    original = wf["2"]["inputs"]["image"]
    patched, report = patch_workflow(wf, pose_image="SHOULD_NOT_APPLY.png")
    assert patched["2"]["inputs"]["image"] == original
    assert "pose_image" in report.skipped


# ----------------------------------------------------------------------------
# 원본 보존 (deep copy)
# ----------------------------------------------------------------------------


def test_patch_does_not_mutate_input() -> None:
    wf = _v38_like()
    snapshot = copy.deepcopy(wf)
    _, _ = patch_workflow(wf, prompt="MUTATED", seed=1, controlnet_strength=0.1)
    # 원본 비교
    assert wf == snapshot


def test_patched_returned_is_independent() -> None:
    wf = _v38_like()
    patched, _ = patch_workflow(wf, prompt="X")
    patched["5"]["inputs"]["text"] = "DIRECT MUTATION"
    assert wf["5"]["inputs"]["text"] == "ORIGINAL POSITIVE"


# ----------------------------------------------------------------------------
# lora_strengths 정규화
# ----------------------------------------------------------------------------


def test_lora_strengths_normalizes_extension() -> None:
    """`.safetensors` 확장자가 있어도 매칭."""
    wf = _v38_like()
    patched, report = patch_workflow(
        wf,
        lora_strengths={
            "pixel_character_sprite_illustrious.safetensors": 0.5,
            "pixelart_up": 0.2,  # 확장자 없이도 매칭
        },
    )
    assert patched["3"]["inputs"]["strength_model"] == 0.5
    assert patched["3"]["inputs"]["strength_clip"] == 0.5
    assert patched["4"]["inputs"]["strength_model"] == 0.2
    assert patched["4"]["inputs"]["strength_clip"] == 0.2
    assert set(report.applied["lora_strengths"]) == {"3", "4"}


def test_lora_strengths_no_match_skips() -> None:
    wf = _v38_like()
    _, report = patch_workflow(wf, lora_strengths={"nonexistent_lora": 0.5})
    assert "lora_strengths" in report.skipped
    assert "lora_strengths" not in report.applied


def test_empty_lora_strengths_dict_is_silently_ignored() -> None:
    wf = _v38_like()
    _, report = patch_workflow(wf, lora_strengths={})
    # 빈 dict 은 falsy → 스킵도 applied 도 없음
    assert "lora_strengths" not in report.applied
    assert "lora_strengths" not in report.skipped


# ----------------------------------------------------------------------------
# PatchReport
# ----------------------------------------------------------------------------


def test_patch_report_truthy_when_any_applied() -> None:
    wf = _v38_like()
    _, report = patch_workflow(wf, prompt="x")
    assert bool(report) is True


def test_patch_report_falsy_when_nothing_applied() -> None:
    wf = _v36_pro_like()
    _, report = patch_workflow(wf, pose_image="x.png")  # V36 Pro 엔 LoadImage 없음
    assert bool(report) is False
    assert "pose_image" in report.skipped


# ----------------------------------------------------------------------------
# multi-KSampler (hires 워크플로우) — 모든 KSampler 동시 패치
# ----------------------------------------------------------------------------


def test_patch_seed_propagates_to_all_ksamplers() -> None:
    """V38 hires/full 처럼 KSampler 가 2개일 때 둘 다 같은 seed 가 박혀야
    stage1 과 hires 결과가 일관."""
    wf = _v38_like()
    wf["16"] = {
        "class_type": "KSampler",
        "inputs": {"seed": 0, "steps": 20, "cfg": 6.0, "sampler_name": "dpmpp_2m", "scheduler": "karras"},
        "_meta": {"title": "KSampler #2 (HiRes)"},
    }
    patched, report = patch_workflow(wf, seed=777, steps=22)
    assert patched["11"]["inputs"]["seed"] == 777
    assert patched["16"]["inputs"]["seed"] == 777
    assert patched["11"]["inputs"]["steps"] == 22
    assert patched["16"]["inputs"]["steps"] == 22
    assert set(report.applied["seed"]) == {"11", "16"}

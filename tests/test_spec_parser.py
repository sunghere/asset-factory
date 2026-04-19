from server import _extract_tasks_from_spec


def test_extract_tasks_from_spec_builds_character_and_ui_tasks() -> None:
    spec = {
        "project": "cat-raising",
        "characters": [
            {
                "id": "ksh",
                "character_prompt": "cute cat",
                "stages": [
                    {"stage": "baby", "output_size": 64, "actions": ["idle", "sleep"]},
                ],
            }
        ],
        "ui_assets": [{"id": "btn_feed", "prompt_hint": "food bowl", "size": 32}],
        "generation_config": {
            "base_prompt": "pixel art",
            "negative_prompt": "realistic",
            "steps": 20,
            "cfg": 7,
            "sampler": "DPM++ 2M",
            "max_colors": 32,
        },
    }

    project, tasks = _extract_tasks_from_spec(spec)

    assert project == "cat-raising"
    assert len(tasks) == 3
    assert any(task["asset_key"] == "ksh_baby_idle" for task in tasks)
    assert any(task["asset_key"] == "btn_feed" for task in tasks)

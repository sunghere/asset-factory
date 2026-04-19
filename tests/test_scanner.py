from pathlib import Path

from scanner import infer_asset_key, scan_directory


def test_infer_asset_key_prefers_skin_stage_action() -> None:
    key = infer_asset_key(Path("sprites/cats/ksh_baby_idle.png"))
    assert key == "ksh_baby_idle"


def test_scan_directory_collects_png_files(tmp_path: Path) -> None:
    image_dir = tmp_path / "ui"
    image_dir.mkdir(parents=True)
    (image_dir / "btn_feed.png").write_bytes(b"fake")
    (image_dir / "readme.txt").write_text("ignore", encoding="utf-8")

    assets = scan_directory(tmp_path)

    assert len(assets) == 1
    assert assets[0]["asset_key"] == "btn_feed"
    assert assets[0]["category"] == "ui"

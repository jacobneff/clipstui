from __future__ import annotations

from clipstui.config import AppConfig, load_config, save_config


def test_load_config_missing_file(tmp_path) -> None:
    path = tmp_path / "config.json"
    config, error = load_config(path)
    assert error is None
    assert config == AppConfig()


def test_save_and_load_config_roundtrip(tmp_path) -> None:
    path = tmp_path / "nested" / "config.json"
    config = AppConfig(
        output_dir="C:/clips",
        output_format="mkv",
        output_template="{tag}_{start}-{end}_{videoid}",
        pad_before_default=2,
        pad_after_default=3,
        tree_root="C:/videos",
        show_hidden=True,
        auto_tag_prefix=True,
    )
    error = save_config(config, path)
    assert error is None
    loaded, error = load_config(path)
    assert error is None
    assert loaded == config


def test_load_config_invalid_json(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not-json", encoding="utf-8")
    config, error = load_config(path)
    assert config == AppConfig()
    assert error is not None

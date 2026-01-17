from __future__ import annotations

import sys

from clipstui.app import _cli_help_text, main
from clipstui.paths import config_path


def test_cli_help_text_includes_config_path() -> None:
    text = _cli_help_text()
    assert "CLIP" in text
    assert str(config_path()) in text


def test_main_help_flag_prints_help(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["clipstui", "-help"])
    main()
    captured = capsys.readouterr()
    assert "clipstui" in captured.out

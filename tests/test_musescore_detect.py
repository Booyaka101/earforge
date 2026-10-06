"""MuseScore detection with fake binaries, and the sheets wrapper's gating."""

import os
import textwrap
from pathlib import Path

import pytest

from earforge.exports import MuseScoreNotFoundError, find_musescore

from earforge import exports


def _fake_musescore(tmp_path: Path, version_line: str) -> Path:
    """A tiny executable that answers --version like MuseScore does."""
    if os.name == "nt":
        script = tmp_path / "mscore.cmd"
        script.write_text("@echo off\r\necho " + version_line + "\r\n")
    else:
        script = tmp_path / "mscore.sh"
        script.write_text(
            textwrap.dedent(f"""\
                #!/bin/sh
                echo "{version_line}"
                """)
        )
        script.chmod(0o755)
    return script


def test_fake_muse_score_4_is_found(tmp_path, monkeypatch):
    fake = _fake_musescore(tmp_path, "MuseScore4 4.7.4")
    monkeypatch.setenv("MUSCRIPTOR_MUSESCORE", str(fake))
    assert find_musescore() == str(fake)


def test_muse_score_3_is_rejected_with_hint(tmp_path, monkeypatch):
    fake = _fake_musescore(tmp_path, "MuseScore 3.6.2")
    monkeypatch.setenv("MUSCRIPTOR_MUSESCORE", str(fake))
    with pytest.raises(MuseScoreNotFoundError) as e:
        find_musescore()
    assert "MuseScore 4" in str(e.value) or "newer" in str(e.value)


def test_env_var_pointing_at_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSCRIPTOR_MUSESCORE", str(tmp_path / "ghost.exe"))
    with pytest.raises(MuseScoreNotFoundError) as e:
        find_musescore()
    assert "not a file" in str(e.value)


def test_sheets_available_agrees_with_finder(monkeypatch):
    fake_available = exports.sheets_available()
    try:
        find_musescore()
        found = True
    except MuseScoreNotFoundError:
        found = False
    assert fake_available == found


def test_export_sheets_zip_gates_on_musescore(tmp_path, monkeypatch):
    """Without MuseScore the export refuses up front with the install hint."""
    monkeypatch.delenv("MUSCRIPTOR_MUSESCORE", raising=False)
    try:
        find_musescore()
        pytest.skip("MuseScore really is installed; gating path not reachable")
    except MuseScoreNotFoundError:
        pass
    with pytest.raises(exports.ExportError) as e:
        exports.export_sheets_zip(b"MThd", None, tmp_path)
    assert "MuseScore" in str(e.value)
    assert not (tmp_path / "sheets").exists()

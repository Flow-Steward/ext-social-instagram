"""Required release validation and optional offline preview have distinct outcomes."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def unavailable_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Path]:
    script = ROOT / ".github/scripts/catalog_check.py"
    spec = importlib.util.spec_from_file_location("release_catalog_check", script)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)

    archive = tmp_path / "acme.preview-1.2.3.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("acme_preview/icon.png", (ROOT / "icon.png").read_bytes())

    def unavailable(_path: str) -> bytes:
        raise OSError("catalog unavailable")

    monkeypatch.setattr(checker, "_fetch", unavailable)
    return checker, archive


def test_release_refuses_archive_when_catalog_cannot_be_fetched(
    unavailable_catalog: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    checker, archive = unavailable_catalog
    monkeypatch.setenv("CI", "true")

    assert checker.main([str(archive)]) != 0


def test_local_offline_preview_is_not_reported_as_catalog_acceptance(
    unavailable_catalog: tuple[ModuleType, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker, archive = unavailable_catalog
    monkeypatch.delenv("CI", raising=False)

    assert checker.main([str(archive)]) == 0
    assert "not checked against the catalog" in capsys.readouterr().out

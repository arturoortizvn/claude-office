"""Tests for the lock verification in scripts/bump_version.py."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bump_version() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "bump_version", REPO / "scripts" / "bump_version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["bump_version"] = module
    spec.loader.exec_module(module)
    return module


def _write_project(root: Path, name: str, declared: str, locked: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{declared}"\n', encoding="utf-8"
    )
    (root / "uv.lock").write_text(
        f'[[package]]\nname = "{name}"\nversion = "{locked}"\nsource = {{ editable = "." }}\n',
        encoding="utf-8",
    )


class TestLockDrift:
    """Tests for _lock_drift."""

    def test_reports_a_lock_that_disagrees_with_its_pyproject(
        self, bump_version: ModuleType, tmp_path: Path
    ) -> None:
        """The 0.24.1 release shipped a root lock still pinned at 0.24.0."""
        _write_project(tmp_path, "claude-office", declared="0.24.1", locked="0.24.0")

        drift = bump_version._lock_drift(tmp_path, (".",))

        assert len(drift) == 1
        assert drift[0].declared == "0.24.1"
        assert drift[0].locked == "0.24.0"

    def test_accepts_a_lock_that_matches(self, bump_version: ModuleType, tmp_path: Path) -> None:
        """A lock at the declared version is not drift."""
        _write_project(tmp_path, "claude-office", declared="0.24.1", locked="0.24.1")

        assert bump_version._lock_drift(tmp_path, (".",)) == []

    def test_reports_a_lock_missing_the_project_entry(
        self, bump_version: ModuleType, tmp_path: Path
    ) -> None:
        """A lock that never locked the project itself is drift, not a silent pass."""
        _write_project(tmp_path, "claude-office", declared="0.24.1", locked="0.24.1")
        (tmp_path / "uv.lock").write_text('[[package]]\nname = "requests"\n', encoding="utf-8")

        drift = bump_version._lock_drift(tmp_path, (".",))

        assert len(drift) == 1
        assert drift[0].locked is None

    def test_repo_lockfiles_match_their_pyproject(self, bump_version: ModuleType) -> None:
        """Regression guard: no lockfile in this repo may lag its pyproject."""
        assert bump_version._lock_drift(REPO) == []


class TestCheckCommand:
    """Tests for cmd_check."""

    def test_check_fails_when_a_lock_lags(
        self, bump_version: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A stale lock must fail --check even when all six locations agree."""
        _write_project(tmp_path, "claude-office", declared="0.24.1", locked="0.24.0")
        monkeypatch.setattr(bump_version, "REPO", tmp_path)
        monkeypatch.setattr(bump_version, "LOCATIONS", (bump_version.LOCATIONS[0],))
        monkeypatch.setattr(bump_version, "LOCKED_PROJECTS", (".",))

        assert bump_version.cmd_check(None) == 1

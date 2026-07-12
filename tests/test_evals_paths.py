"""Tests for evals.paths.find_repo_root (robust repo-root discovery)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.paths import REPO_ROOT_ENV_VAR, find_repo_root


def test_finds_marker_from_nested_fake_dir(tmp_path: Path) -> None:
    """Walking up from an arbitrarily deep fake path finds the pyproject.toml root."""
    (tmp_path / "pyproject.toml").write_text("")
    nested = tmp_path / "a" / "b" / "c" / "d"
    nested.mkdir(parents=True)
    fake_file = nested / "harness.py"

    assert find_repo_root(fake_file) == tmp_path.resolve()


def test_finds_marker_from_worktree_style_layout(tmp_path: Path) -> None:
    """A worktree-style layout (.claude/worktrees/<id>/src/evals/harness.py) still
    resolves to the worktree's own repo root, not some fixed ancestor depth."""
    repo_root = tmp_path / ".claude" / "worktrees" / "some-id"
    src_dir = repo_root / "src" / "evals"
    src_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("")

    assert find_repo_root(src_dir / "harness.py") == repo_root.resolve()


def test_raises_when_no_marker_found(tmp_path: Path) -> None:
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        find_repo_root(nested / "harness.py")


def test_env_var_overrides_marker_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override_root = tmp_path / "override"
    override_root.mkdir()
    monkeypatch.setenv(REPO_ROOT_ENV_VAR, str(override_root))

    nested = tmp_path / "unrelated" / "deep"
    nested.mkdir(parents=True)

    assert find_repo_root(nested / "harness.py") == override_root.resolve()

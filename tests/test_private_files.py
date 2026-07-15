"""
General pytest tests for this package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import private_files as pf

pytestmark = pytest.mark.skipif(
        sys.platform == "win32", reason="tests target the UNIX private-dir implementation and its permission model"
    )

APP_NAME = "testapp"


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


@pytest.fixture
def sandbox_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the shared private-dir root to a temp directory and clear the memoized
    caches (module-level and manager-level) so each test starts from a clean, isolated state."""
    root = tmp_path / ".private"
    monkeypatch.setattr(pf, "UNIX_PRIVATE_DIR_ROOT_PATH", root)
    pf._get_shared_private_dir.cache_clear()
    pf._create_shared_private_dir.cache_clear()
    pf._get_private_files_manager.cache_clear()
    yield root
    pf._get_shared_private_dir.cache_clear()
    pf._create_shared_private_dir.cache_clear()
    pf._get_private_files_manager.cache_clear()


@pytest.fixture
def manager(sandbox_root: Path) -> pf.PrivateFilesManager:
    return pf.PrivateFilesManager(app_name=APP_NAME)


# --- PrivateFilesManager: shared root ---


def test_get_shared_root_dir(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    assert manager.get_shared_root_dir() == sandbox_root.resolve()


def test_create_shared_root_dir_creates_and_locks_down(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    shared_dir = manager.create_shared_root_dir()
    assert shared_dir.is_dir()
    assert _mode(shared_dir) == 0o700


def test_create_shared_root_dir_rejects_bad_permissions(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    sandbox_root.mkdir(mode=0o700, parents=True)
    sandbox_root.chmod(0o755)
    with pytest.raises(PermissionError):
        manager.create_shared_root_dir()


# --- PrivateFilesManager: app-specific root ---


def test_get_root_dir_with_app_name(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    assert manager.get_root_dir() == (sandbox_root / APP_NAME).resolve()


def test_get_root_dir_without_app_name_is_shared_root(sandbox_root: Path) -> None:
    mgr = pf.PrivateFilesManager()
    assert mgr.get_root_dir() == sandbox_root.resolve()


def test_get_root_dir_rejects_app_name_traversal(sandbox_root: Path) -> None:
    mgr = pf.PrivateFilesManager(app_name="../escape")
    with pytest.raises(ValueError):
        mgr.get_root_dir()


def test_get_root_dir_is_cached_per_instance(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    first = manager.get_root_dir()
    manager.app_name = "different"
    second = manager.get_root_dir()
    assert second == first


def test_create_root_dir_creates_nested_path_with_locked_permissions(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    root_dir = manager.create_root_dir()
    assert root_dir.is_dir()
    assert _mode(root_dir) == 0o700
    assert _mode(sandbox_root) == 0o700


def test_create_root_dir_fixes_existing_bad_permissions(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    root_dir = manager.create_root_dir()
    root_dir.chmod(0o755)
    manager._root_dir_created = False
    fixed = manager.create_root_dir()
    assert _mode(fixed) == 0o700


# --- PrivateFilesManager: subdirectories ---


def test_get_private_dir_dot_returns_root(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    assert manager.get_private_dir(".") == manager.get_root_dir()


def test_get_private_dir_does_not_create(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    nested = manager.get_private_dir("a/b")
    assert nested == manager.get_root_dir() / "a" / "b"
    assert not nested.exists()


def test_get_private_dir_rejects_traversal(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(ValueError):
        manager.get_private_dir("../escape")


def test_create_private_dir_creates_every_level_locked_down(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    nested = manager.create_private_dir("a/b/c")
    root_dir = manager.get_root_dir()
    assert nested == root_dir / "a" / "b" / "c"
    for partial in (root_dir / "a", root_dir / "a" / "b", root_dir / "a" / "b" / "c"):
        assert partial.is_dir()
        assert _mode(partial) == 0o700


def test_create_private_dir_fixes_permissions_at_every_level(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    leaf = manager.create_private_dir("a/b")
    leaf.chmod(0o755)
    leaf.parent.chmod(0o755)
    fixed = manager.create_private_dir("a/b")
    assert _mode(fixed) == 0o700
    assert _mode(fixed.parent) == 0o700


def test_create_private_dir_is_idempotent(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    first = manager.create_private_dir("a/b")
    second = manager.create_private_dir("a/b")
    assert first == second
    assert second.is_dir()


def test_create_private_dir_rejects_traversal(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(ValueError):
        manager.create_private_dir("../escape")


def test_delete_private_dir_removes_tree_scoped_to_app(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    nested = manager.create_private_dir("a/b")
    (nested / "file.txt").write_text("data")
    manager.delete_private_dir("a")
    assert not (manager.get_root_dir() / "a").exists()


def test_delete_private_dir_is_scoped_to_the_owning_manager(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    # Regression test: delete_private_dir must resolve "secret" against this manager's own
    # app-specific root, not the shared root used by the default (app_name=None) manager.
    other = pf.PrivateFilesManager(app_name="otherapp")
    other.create_private_dir("secret")
    manager.create_private_dir("secret")
    manager.delete_private_dir("secret")
    assert not (manager.get_root_dir() / "secret").exists()
    assert (other.get_root_dir() / "secret").is_dir()


def test_delete_private_dir_rejects_deleting_shared_root(sandbox_root: Path) -> None:
    mgr = pf.PrivateFilesManager()
    mgr.create_root_dir()
    with pytest.raises(ValueError):
        mgr.delete_private_dir(".")


def test_delete_private_dir_missing_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(NotADirectoryError):
        manager.delete_private_dir("does-not-exist")


def test_verify_private_dir_success(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    manager.create_private_dir("a/b")
    result = manager.verify_private_dir("a/b")
    assert result == manager.get_root_dir() / "a" / "b"


def test_verify_private_dir_dot_checks_root_itself(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    # Regression test: verifying "." must still check the app root directory itself,
    # not silently succeed because the walked relative path has zero components.
    with pytest.raises(NotADirectoryError):
        manager.verify_private_dir(".")


def test_verify_private_dir_missing_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(NotADirectoryError):
        manager.verify_private_dir("a/b")


def test_verify_private_dir_bad_permissions_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    nested = manager.create_private_dir("a/b")
    nested.chmod(0o755)
    with pytest.raises(PermissionError):
        manager.verify_private_dir("a/b")


def test_verify_private_dir_rejects_traversal(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(ValueError):
        manager.verify_private_dir("../escape")


# --- PrivateFilesManager: files ---


def test_get_private_file_without_create_parent_raises_when_missing(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(NotADirectoryError):
        manager.get_private_file("secret.txt")


def test_get_private_file_with_create_parent(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    file_path = manager.get_private_file("secret.txt", create_parent=True)
    assert isinstance(file_path, Path)
    assert file_path == manager.get_root_dir() / "secret.txt"
    assert not file_path.exists()  # the file itself is never created by this call
    assert manager.get_root_dir().is_dir()


def test_get_private_file_nested_subdir(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    file_path = manager.get_private_file("secret.txt", create_parent=True, subdir="a/b")
    assert file_path == manager.get_root_dir() / "a" / "b" / "secret.txt"
    assert file_path.parent.is_dir()


def test_get_private_file_rejects_filename_traversal(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(ValueError):
        manager.get_private_file("../escape.txt", create_parent=True)


def test_open_write_then_read_round_trip(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w") as f:
        f.write("hello world")

    file_path = manager.get_private_file("secret.txt")
    assert _mode(file_path) == 0o600

    with manager.open("secret.txt", "r") as f:
        assert f.read() == "hello world"


def test_open_binary_round_trip(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.bin", "wb") as f:
        f.write(b"\x00\x01\x02")

    with manager.open("secret.bin", "rb") as f:
        assert f.read() == b"\x00\x01\x02"


def test_open_read_missing_file_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("exists.txt", "w") as f:
        f.write("x")
    with pytest.raises(FileNotFoundError):
        manager.open("missing.txt", "r")


def test_open_read_missing_parent_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(NotADirectoryError):
        manager.open("secret.txt", "r")


def test_open_read_mode_does_not_force_create_parent(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(NotADirectoryError):
        manager.open("secret.txt", "r", create_parent=False)


def test_open_explicit_create_parent_overrides_mode_inference(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    # "r" mode alone wouldn't create the parent directory, but an explicit create_parent=True should,
    # even though the read itself still fails because the file itself doesn't exist.
    with pytest.raises(FileNotFoundError):
        manager.open("secret.txt", "r", subdir="a/b", create_parent=True)
    assert (manager.get_root_dir() / "a" / "b").is_dir()


# --- private_files() ---


def test_private_files_is_cached_per_app_name(sandbox_root: Path) -> None:
    first = pf.private_files(APP_NAME)
    second = pf.private_files(APP_NAME)
    assert first is second


def test_private_files_distinct_per_app_name(sandbox_root: Path) -> None:
    a = pf.private_files("app-a")
    b = pf.private_files("app-b")
    assert a is not b
    assert a.get_root_dir() != b.get_root_dir()


def test_all_exports_are_importable() -> None:
    for name in pf.__all__:
        assert hasattr(pf, name)

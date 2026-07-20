"""
General pytest tests for this package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import private_files as pf
import private_files.private_files_manager as pf_manager
import private_files.util as pf_util

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
    monkeypatch.setattr(pf_util, "UNIX_PRIVATE_DIR_ROOT_PATH", root)
    pf_util._get_base_data_dir.cache_clear()
    pf_manager._get_private_files_manager.cache_clear()
    yield root
    pf_util._get_base_data_dir.cache_clear()
    pf_manager._get_private_files_manager.cache_clear()


@pytest.fixture
def manager(sandbox_root: Path) -> pf.PrivateFilesManager:
    return pf.PrivateFilesManager(app_name=APP_NAME)


# --- PrivateFilesManager: shared root ---


def test_create_root_dir_also_creates_and_locks_down_shared_root(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    manager.create_root_dir()
    assert sandbox_root.is_dir()
    assert _mode(sandbox_root) == 0o700


def test_create_root_dir_rejects_bad_shared_root_permissions(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    sandbox_root.mkdir(mode=0o700, parents=True)
    sandbox_root.chmod(0o755)
    with pytest.raises(PermissionError):
        manager.create_root_dir()


# --- PrivateFilesManager: app-specific root ---


def test_get_root_dir_with_app_name(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    assert manager.get_root_dir() == (sandbox_root / APP_NAME).resolve()


def test_get_root_dir_without_app_name_uses_default_app_name(sandbox_root: Path) -> None:
    mgr = pf.PrivateFilesManager()
    assert mgr.get_root_dir() == (sandbox_root / pf.DEFAULT_APP_NAME).resolve()


def test_get_root_dir_rejects_app_name_traversal(sandbox_root: Path) -> None:
    # Validated eagerly in __init__ now, since the base class needs its stop-scan paths resolved
    # at construction time regardless.
    with pytest.raises(ValueError):
        pf.PrivateFilesManager(app_name="../escape")


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
    manager._dir_created = False
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
    # app-specific root, not some other manager's root.
    other = pf.PrivateFilesManager(app_name="otherapp")
    other.create_private_dir("secret")
    manager.create_private_dir("secret")
    manager.delete_private_dir("secret")
    assert not (manager.get_root_dir() / "secret").exists()
    assert (other.get_root_dir() / "secret").is_dir()


def test_delete_private_dir_rejects_deleting_own_root(sandbox_root: Path) -> None:
    mgr = pf.PrivateFilesManager()
    mgr.create_root_dir()
    with pytest.raises(ValueError):
        mgr.delete_private_dir(".")


def test_delete_private_dir_missing_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(NotADirectoryError):
        manager.delete_private_dir("does-not-exist")


def test_delete_app_data_removes_root_and_contents(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    nested = manager.create_private_dir("a/b")
    (nested / "file.txt").write_text("data")
    manager.delete_app_data()
    assert not manager.get_root_dir().exists()


def test_delete_app_data_missing_is_a_noop(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    assert not manager.get_root_dir().exists()
    manager.delete_app_data()  # must not raise
    assert not manager.get_root_dir().exists()


def test_delete_app_data_does_not_affect_other_managers(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    other = pf.PrivateFilesManager(app_name="otherapp")
    other.create_private_dir(".")
    manager.create_private_dir(".")
    manager.delete_app_data()
    assert not manager.get_root_dir().exists()
    assert other.get_root_dir().is_dir()


def test_delete_app_data_allows_recreation_afterward(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    manager.create_root_dir()
    manager.delete_app_data()
    # _dir_created must be reset so a later create doesn't skip straight to the stale
    # "already created" fast path and then fail the existence check.
    root_dir = manager.create_root_dir()
    assert root_dir.is_dir()
    assert _mode(root_dir) == 0o700


def test_delete_app_data_not_available_on_private_dir_manager(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    sub = manager.get_subdir_manager("a")
    assert not hasattr(sub, "delete_app_data")


def test_verify_private_dir_success(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    manager.create_private_dir("a/b")
    result = manager.verify_private_dir("a/b")
    assert result == manager.get_root_dir() / "a" / "b"


def test_verify_private_dir_dot_checks_root_itself(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    # Regression test: verifying "." must still check the app root directory itself,
    # not silently succeed because the walked relative path has zero components.
    with pytest.raises(FileNotFoundError):
        manager.verify_private_dir(".")


def test_verify_private_dir_missing_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(FileNotFoundError):
        manager.verify_private_dir("a/b")


def test_verify_private_dir_bad_permissions_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    nested = manager.create_private_dir("a/b")
    nested.chmod(0o755)
    with pytest.raises(PermissionError):
        manager.verify_private_dir("a/b")


def test_verify_private_dir_rejects_traversal(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    # Root must exist first, or verify_root_dir() raises FileNotFoundError before the traversal
    # check on subdir is ever reached.
    manager.create_root_dir()
    with pytest.raises(ValueError):
        manager.verify_private_dir("../escape")


# --- PrivateFilesManager: get_subdir_manager() ---


def test_get_subdir_manager_creates_by_default(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    sub = manager.get_subdir_manager("a/b")
    assert sub.get_root_dir() == manager.get_private_dir("a/b")
    assert sub.get_root_dir().is_dir()
    assert _mode(sub.get_root_dir()) == 0o700


def test_get_subdir_manager_create_false_touches_nothing(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    sub = manager.get_subdir_manager("a/b", create=False)
    assert sub.get_root_dir() == manager.get_private_dir("a/b")
    assert not manager.get_root_dir().exists()
    assert not sub.get_root_dir().exists()


def test_get_subdir_manager_is_scoped_to_its_own_subtree(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    sub = manager.get_subdir_manager("a/b")
    with pytest.raises(ValueError):
        sub.get_private_dir("../escape")
    with pytest.raises(ValueError):
        sub.delete_private_dir(".")


def test_get_subdir_manager_files_are_independent_of_parent(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    sub = manager.get_subdir_manager("a/b")
    with sub.open("secret.txt", "w") as f:
        f.write("nested secret")
    with sub.open("secret.txt", "r") as f:
        assert f.read() == "nested secret"
    assert (manager.get_private_dir("a/b") / "secret.txt").is_file()


# --- PrivateFilesManager: files ---


def test_get_private_file_without_create_parent_raises_when_missing(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(FileNotFoundError):
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
    with pytest.raises(FileNotFoundError):
        manager.open("secret.txt", "r")


def test_open_read_mode_does_not_force_create_parent(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with pytest.raises(FileNotFoundError):
        manager.open("secret.txt", "r", create_parent=False)


def test_open_explicit_create_parent_overrides_mode_inference(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    # "r" mode alone wouldn't create the parent directory, but an explicit create_parent=True should,
    # even though the read itself still fails because the file itself doesn't exist.
    with pytest.raises(FileNotFoundError):
        manager.open("secret.txt", "r", subdir="a/b", create_parent=True)
    assert (manager.get_root_dir() / "a" / "b").is_dir()


# --- PrivateFilesManager: open() passphrase encryption ---


def test_open_passphrase_round_trip(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", passphrase="hunter2") as f:
        f.write("top secret")
    with manager.open("secret.txt", "r", passphrase="hunter2") as f:
        assert f.read() == "top secret"


def test_open_passphrase_writes_encrypted_bytes_on_disk(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", passphrase="hunter2") as f:
        f.write("top secret")
    raw = manager.get_private_file("secret.txt").read_bytes()
    assert b"top secret" not in raw
    assert manager.looks_encrypted("secret.txt")


def test_open_wrong_passphrase_raises_decryption_error(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", passphrase="hunter2") as f:
        f.write("top secret")
    with pytest.raises(pf.DecryptionError):
        manager.open("secret.txt", "r", passphrase="wrong")


def test_open_check_encryption_false_by_default_returns_ciphertext(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", passphrase="hunter2") as f:
        f.write("top secret")
    with manager.open("secret.txt", "rb") as f:
        assert f.read().startswith(b"PRVFILE")


def test_open_check_encryption_without_passphrase_raises(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", passphrase="hunter2") as f:
        f.write("top secret")
    with pytest.raises(pf.PassphraseRequiredError):
        manager.open("secret.txt", "r", check_encryption=True)


def test_open_check_encryption_with_atomic_update_and_no_passphrase_still_raises(
            sandbox_root: Path, manager: pf.PrivateFilesManager
        ) -> None:
    # Regression test: atomic_update alone routes through the wrapper (independently of
    # passphrase), so check_encryption must not be silently skipped in that combination.
    with manager.open("secret.txt", "w", passphrase="hunter2") as f:
        f.write("top secret")
    with pytest.raises(pf.PassphraseRequiredError):
        manager.open("secret.txt", "r", atomic_update=True, check_encryption=True)


# --- PrivateFilesManager: open() atomic_update ---


def test_open_atomic_update_write_then_read_round_trip(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", atomic_update=True) as f:
        f.write("hello atomic")
    with manager.open("secret.txt", "r") as f:
        assert f.read() == "hello atomic"


def test_open_atomic_update_does_not_leave_temp_file_behind(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", atomic_update=True) as f:
        f.write("hello atomic")
    file_path = manager.get_private_file("secret.txt")
    assert not file_path.with_name(file_path.name + ".tmp").exists()


def test_open_atomic_update_leaves_target_untouched_until_close(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    file_path = manager.get_private_file("secret.txt", create_parent=True)
    file_path.write_text("original")
    f = manager.open("secret.txt", "w", atomic_update=True)
    f.write("new content")
    assert file_path.read_text() == "original"
    f.close()
    assert file_path.read_text() == "new content"


def test_open_atomic_update_exclusive_mode_raises_if_exists(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w") as f:
        f.write("existing")
    with pytest.raises(FileExistsError):
        manager.open("secret.txt", "x", atomic_update=True)
    with manager.open("secret.txt", "r") as f:
        assert f.read() == "existing"


def test_open_atomic_update_append_round_trip(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w") as f:
        f.write("hello ")
    with manager.open("secret.txt", "a", atomic_update=True) as f:
        f.write("world")
    with manager.open("secret.txt", "r") as f:
        assert f.read() == "hello world"


def test_open_atomic_update_with_passphrase_round_trip(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    with manager.open("secret.txt", "w", atomic_update=True, passphrase="hunter2") as f:
        f.write("top secret")
    with manager.open("secret.txt", "r", passphrase="hunter2") as f:
        assert f.read() == "top secret"


def test_open_atomic_update_cleans_up_temp_file_on_replace_failure(
            sandbox_root: Path, manager: pf.PrivateFilesManager, monkeypatch: pytest.MonkeyPatch
        ) -> None:
    def failing_replace(src: object, dst: object) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", failing_replace)
    f = manager.open("secret.txt", "w", atomic_update=True)
    f.write("data")
    with pytest.raises(OSError):
        f.close()
    file_path = manager.get_private_file("secret.txt")
    assert not file_path.exists()
    assert not file_path.with_name(file_path.name + ".tmp").exists()


def test_open_atomic_update_explicit_abort_discards_write(sandbox_root: Path, manager: pf.PrivateFilesManager) -> None:
    file_path = manager.get_private_file("secret.txt", create_parent=True)
    file_path.write_text("original")
    f = manager.open("secret.txt", "w", atomic_update=True)
    f.write("new content")
    f.abort()
    f.close()
    assert file_path.read_text() == "original"
    assert not file_path.with_name(file_path.name + ".tmp").exists()


def test_open_atomic_update_text_mode_with_block_aborts_on_exception(
            sandbox_root: Path, manager: pf.PrivateFilesManager
        ) -> None:
    # Text mode is the default returned object (an _AbortableTextIOWrapper around WrappedFile),
    # not a WrappedFile itself -- this exercises that forwarding path specifically.
    file_path = manager.get_private_file("secret.txt", create_parent=True)
    file_path.write_text("original")
    with pytest.raises(RuntimeError), manager.open("secret.txt", "w", atomic_update=True) as f:
        f.write("partial")
        raise RuntimeError("boom")
    assert file_path.read_text() == "original"
    assert not file_path.with_name(file_path.name + ".tmp").exists()


def test_open_atomic_update_binary_mode_with_block_aborts_on_exception(
            sandbox_root: Path, manager: pf.PrivateFilesManager
        ) -> None:
    file_path = manager.get_private_file("secret.bin", create_parent=True)
    file_path.write_bytes(b"original")
    with pytest.raises(RuntimeError), manager.open("secret.bin", "wb", atomic_update=True) as f:
        f.write(b"partial")
        raise RuntimeError("boom")
    assert file_path.read_bytes() == b"original"
    assert not file_path.with_name(file_path.name + ".tmp").exists()


def test_open_non_atomic_with_block_aborts_on_exception_preserves_original(
            sandbox_root: Path, manager: pf.PrivateFilesManager
        ) -> None:
    # "r+" with no atomic_update and a passphrase forces WrappedFile's non-atomic branch (deferred
    # in-place writeback to the real file handle at close()), as opposed to the atomic_update
    # branch exercised by the tests above.
    with manager.open("secret.txt", "w", passphrase="hunter2") as f:
        f.write("original")
    with pytest.raises(RuntimeError), manager.open("secret.txt", "r+", passphrase="hunter2") as f:
        f.seek(0)
        f.write("partial")
        raise RuntimeError("boom")
    with manager.open("secret.txt", "r", passphrase="hunter2") as f:
        assert f.read() == "original"


# --- get_private_files() ---


def test_get_private_files_is_cached_per_app_name(sandbox_root: Path) -> None:
    first = pf.get_private_files(APP_NAME)
    second = pf.get_private_files(APP_NAME)
    assert first is second


def test_get_private_files_distinct_per_app_name(sandbox_root: Path) -> None:
    a = pf.get_private_files("app-a")
    b = pf.get_private_files("app-b")
    assert a is not b
    assert a.get_root_dir() != b.get_root_dir()


def test_all_exports_are_importable() -> None:
    for name in pf.__all__:
        assert hasattr(pf, name)

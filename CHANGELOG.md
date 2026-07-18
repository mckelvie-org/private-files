# CHANGELOG

## 3.1.0 (2026-07-18)

- _Add release notes here._

## [3.1.0]

### Added

- `PrivateDirManager`: `PrivateFilesManager`'s directory-walking, creation, and
  permission-fixing/verifying logic is now factored into this reusable base class, which can be
  constructed directly against any directory, not just the shared private root -- handy for
  testing code that uses `PrivateFilesManager` against a throwaway directory.
- `get_subdir_manager(subdir, create=True) -> PrivateDirManager`: returns a new, independent
  manager scoped to a subdirectory. It behaves like a full manager rooted at that subdirectory,
  but never creates, fixes, or verifies permissions on anything at or above it, including the
  original manager's own root. Pass `create=False` to resolve and validate the subdirectory path
  without touching the filesystem.

### Changed

- A missing directory (e.g. reading a file without `create_parent=True` when its parent doesn't
  exist yet) now raises `FileNotFoundError`, distinct from `NotADirectoryError` for a path that
  exists but isn't a directory.
- `PrivateFilesManager(app_name=...)` with a path-traversing `app_name` now raises `ValueError`
  immediately at construction, rather than lazily on the first call that resolves the root
  directory.

### Removed

- `PrivateFilesManager.create_shared_root_dir()`. Creating (and locking down) the shared root is
  now an automatic side effect of `create_root_dir()`. `get_shared_root_dir()` is unaffected.

### Fixed

- The shared root's own permissions were silently never verified by `verify_private_dir()`
  (it walked using the wrong stop-path field internally), contradicting the documented "checked,
  but never silently fixed" contract for the shared root.

## [3.0.1]

### Added

- `atomic_update=True` on `PrivateFilesManager.open()`: writes go to a temporary file
  (`filename` + `temp_file_extension`, default `.tmp`) first, which is renamed over the target
  only once the file is closed successfully, so the target is never left partially written. Fully
  atomic on Linux/macOS; on Windows there's a brief window where the target is removed before the
  temp file is renamed into place. Has no effect on modes that don't write (e.g. plain `"r"`).
- `abort()` on the file object returned by `open()`: marks the pending write to be discarded
  instead of committed on the next close. This is triggered automatically if a `with` block exits
  because of an exception, or if the file is garbage-collected without ever having been closed --
  in both cases nothing further needs to be done by the caller. It can also be called explicitly
  to deliberately discard a write in progress without raising an exception to trigger it.
- `AbortableTextIO` / `AbortableBinaryIO`: the return type of `open()` when `atomic_update=True`
  is passed explicitly, so `abort()` is available without a cast. Exported at the package level.

## [3.0.0]

### Changed

- Renamed the package's single entry point `private_files()` -> `get_private_files()`, since
  naming a function the same as its own package is bad form.

## [2.0.0]

### Added

- Optional passphrase encryption on `PrivateFilesManager.open()` (`passphrase=`): Argon2id key
  derivation + AES-256-GCM authenticated encryption, applied as a whole rather than streamed, so
  every mode (including `"r+"`, `"a"`/`"a+"`, and arbitrary `seek()`) works normally.
  `check_encryption=` opts into detecting a missing passphrase on an encrypted file up front.
  New `PrivateFilesManager.looks_encrypted()`, and `DecryptionError` / `NotEncryptedError` /
  `PassphraseRequiredError` exceptions.

### Changed

- Collapsed the ~27 individual `Literal[...]`-mode overloads on `PrivateFilesManager.open()` down
  to two, backed by new public `OpenTextMode` / `OpenBinaryMode` type aliases. Fixes several mode
  strings (e.g. `"xt"`, `"wt+"`, `"at+"`) that were silently missing from the old, hand-written
  overload list.
- Cached internal functions now preserve their real call signature under static type checking;
  previously `@functools.cache` erased parameter names/types in favor of a generic
  `(*args: Hashable, **kwargs: Hashable)` signature.

### Removed

- All flat module-level functions except the package's single entry point, which is renamed
  `get_private_files_manager()` -> `private_files()`. `get_private_app_dir()`,
  `create_private_app_dir()`, `get_private_dir()`, `create_private_dir()`, `delete_private_dir()`,
  `verify_private_dir()`, `get_private_app_file()`, `open_private_app_file()`,
  `get_shared_private_dir()`, and `create_shared_private_dir()` are gone -- call the equivalent
  method on `private_files(app_name)` (a cached `PrivateFilesManager`) instead.

## [1.0.0]

Initial release of `private-files`.

### Added

- `PrivateFilesManager` class and equivalent module-level functions for managing a per-user,
  per-application private directory tree, with `0700`/`0600` permissions enforced and repaired
  automatically.
- Shared private root resolution: `~/.private` on Linux/macOS, the non-roaming app-data directory
  on Windows (via `platformdirs`).
- Path-traversal-safe subdirectory and file resolution (`get_private_dir`, `create_private_dir`,
  `delete_private_dir`, `verify_private_dir`).
- `open_private_app_file()` / `PrivateFilesManager.open()`, a drop-in `open()` replacement with
  mode-based `@overload` typing and automatic parent-directory creation and file permissioning.

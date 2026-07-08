# CHANGELOG

## 1.0.1 (2026-07-08)

- _Add release notes here._

## [1.0.1]

### Changed

- Collapsed the ~27 individual `Literal[...]`-mode overloads on `open_private_app_file()` /
  `PrivateFilesManager.open()` down to two, backed by new public `OpenTextMode` / `OpenBinaryMode`
  type aliases. Fixes several mode strings (e.g. `"xt"`, `"wt+"`, `"at+"`) that were silently
  missing from the old, hand-written overload list.
- Cached functions (`get_shared_private_dir`, `create_shared_private_dir`,
  `get_private_files_manager`) now preserve their real call signature under static type checking;
  previously `@functools.cache` erased parameter names/types in favor of a generic
  `(*args: Hashable, **kwargs: Hashable)` signature.

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

# CHANGELOG

## 1.0.0 (2026-07-08)

- _Add release notes here._

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

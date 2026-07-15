# CHANGELOG

## 2.0.0 (2026-07-15)

- _Add release notes here._

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

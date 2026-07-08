# private-files

[![CI](https://img.shields.io/badge/CI-passing-brightgreen.svg)](https://github.com/mckelvie-org/private-files/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v1.0.0-blue.svg)](https://pypi.org/project/private-files/1.0.0/)
[![Python versions](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13%20|%203.14-blue.svg)](https://pypi.org/project/private-files/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`private-files`: Manage secret/private files in an os-independent way.

Applications that need to persist sensitive data on a user's machine &mdash; API tokens, session cookies,
credentials, profile data &mdash; need somewhere to put it that isn't the world-readable home directory
clutter of `~/.config` or `~/.myapp`. `private-files` gives every application its own subdirectory of a
single, locked-down, user-wide private root, and enforces `0700`/`0600` permissions on directories and
files it creates so secrets are never accidentally left group- or world-readable.

## Highlights

- **One shared private root per user.** On Linux and macOS this is `~/.private`; on Windows it's the
  non-roaming application-data directory (via [`platformdirs`](https://pypi.org/project/platformdirs/)),
  which is not synced to the cloud or shared across devices.
- **Per-application subdirectories.** Each app gets its own subdirectory of the shared root, named after
  an `app_name` you choose, so multiple applications can share the same machine without stepping on each
  other's secrets.
- **Permissions are enforced, not assumed.** Directories created by this package are `chmod 0700`; files
  opened for writing are `chmod 0600`. Existing app-specific directories with the wrong permissions are
  fixed automatically; the shared root itself is only ever checked, never silently "fixed," since it may
  be shared with other applications.
- **Path-traversal safe.** Subdirectory and filename arguments are resolved and checked to ensure they
  stay within the intended app directory &mdash; a `subdir` or `filename` of `"../../etc/passwd"` raises
  `ValueError` instead of silently escaping the sandbox.
- **Two equivalent APIs.** A `PrivateFilesManager` class for when you're working with one application
  repeatedly (it caches the resolved paths), and a set of flat module-level functions for one-off calls,
  both backed by the same cached manager instances.
- **Drop-in `open()` replacement.** `open_private_app_file()` / `PrivateFilesManager.open()` behave like
  the builtin `open()` &mdash; including `@overload`-based mode-based return-type inference (`TextIO` vs
  `BinaryIO`) &mdash; but resolve the path into the private directory and create parent directories and
  fix file permissions automatically.
- **Fully typed**, `mypy --strict` clean, zero required dependencies beyond `platformdirs`.

## Installation

```bash
pip install private-files
```

## Quick Start

```python
from private_files import open_private_app_file, get_private_app_dir

# Write a secret. Parent directories are created automatically (mode 0700),
# and the file itself ends up with mode 0600.
with open_private_app_file("api-token.txt", "w", app_name="myapp") as f:
    f.write("super-secret-token")

# Read it back later.
with open_private_app_file("api-token.txt", "r", app_name="myapp") as f:
    token = f.read()

# Find out where it lives on disk, without opening it.
print(get_private_app_dir(app_name="myapp"))
# -> /home/alice/.private/myapp   (Linux/macOS)
# -> C:\Users\alice\AppData\Local\myapp\myapp   (Windows)
```

If your application makes several calls, prefer a `PrivateFilesManager`, which resolves and caches its
paths once instead of on every call:

```python
from private_files import PrivateFilesManager

files = PrivateFilesManager(app_name="myapp")

with files.open("api-token.txt", "w") as f:
    f.write("super-secret-token")

with files.open("session/cookies.json", "w", create_parent=True) as f:
    f.write("{}")

files.delete_private_dir("session")
```

`get_private_files_manager(app_name)` returns a process-wide cached `PrivateFilesManager` for a given
`app_name`, which is what all of the flat module-level functions use internally &mdash; so mixing the two
styles for the same `app_name` shares the same cached, verified paths.

## Concepts

### Shared root vs. app-specific directory

There are two levels of directory:

- The **shared private root** is one directory per user, shared by every application using this package:
  `~/.private` on Linux/macOS, or the non-roaming app-data directory on Windows. It must already have
  (or be given) permissions `0700`. This package will create it if missing but will **not** silently fix
  its permissions if it already exists with the wrong mode, since it may be shared with other
  applications you don't control &mdash; instead it raises `PermissionError` so you can decide what to do.
- The **application-specific directory** is a subdirectory of the shared root named after your `app_name`
  (e.g. `~/.private/myapp`). Unlike the shared root, this package **does** own it, so it actively enforces
  and repairs `0700` permissions on it and any subdirectories you create within it.

Passing `app_name=None` (the default) targets the shared root itself rather than a per-application
subdirectory.

### Subdirectories and files

Within an application's directory you can create arbitrarily nested subdirectories (`subdir="cache/v2"`)
and files within them. Every directory component created by `create_private_dir()` (or implicitly by
`create_parent=True`) gets its permissions verified and, if necessary, corrected to `0700`. Every
directory checked by `verify_private_dir()` must already be `0700`, or a `PermissionError` is raised.

All `subdir` and `filename` arguments are resolved and checked against the directory they're supposed to
be contained within; anything that would resolve outside of it (via `..`, absolute paths outside the
tree, symlinks, etc.) raises `ValueError` rather than being silently permitted.

## API Reference

### `PrivateFilesManager`

```python
class PrivateFilesManager:
    def __init__(self, app_name: str | None = None): ...
```

An object bound to a single `app_name` (or `None` for the shared root) that resolves and caches its
directory paths across calls.

| Method | Description |
| --- | --- |
| `get_shared_root_dir() -> Path` | The shared private root (e.g. `~/.private`). Computed, not created. |
| `create_shared_root_dir() -> Path` | Create the shared root if missing (mode `0700`); raise `PermissionError` if it exists with the wrong mode. |
| `get_root_dir() -> Path` | This manager's app-specific directory. Computed, not created. |
| `create_root_dir() -> Path` | Create the app-specific directory (and the shared root, if needed), fixing permissions at every level. |
| `get_private_dir(subdir) -> Path` | Resolve `subdir` under the app directory. Does not create anything. |
| `create_private_dir(subdir) -> Path` | Create `subdir` (and every intermediate component) under the app directory, mode `0700`. |
| `delete_private_dir(subdir) -> None` | Recursively delete `subdir`. No-op if it doesn't exist. Raises `ValueError` if `subdir` resolves to the shared root itself. |
| `verify_private_dir(subdir) -> Path` | Raise `NotADirectoryError`/`PermissionError` unless `subdir` (and everything above it, up to the shared root) exists with mode `0700`. |
| `get_private_file(filename, *, create_parent=False, subdir=".") -> Path` | Resolve the full path to a file. Verifies (or creates, if `create_parent=True`) its parent directory. The file itself is never created. |
| `open(filename, mode="r", *, subdir=".", create_parent=False, **kwargs) -> IO` | Like builtin `open()`, but resolved into the app directory. Parent directories are auto-created for write/append/exclusive-create modes (or when `create_parent=True`); files opened for writing get mode `0600`. |

`subdir="."` refers to the app directory itself. All `subdir`/`filename` parameters accept `str | Path`.

### Module-level functions

Thin wrappers around a cached `PrivateFilesManager` per `app_name`, for callers that don't want to hold
onto a manager instance themselves:

| Function | Equivalent to |
| --- | --- |
| `get_shared_private_dir() -> Path` | `PrivateFilesManager().get_shared_root_dir()` |
| `create_shared_private_dir() -> Path` | `PrivateFilesManager().create_shared_root_dir()` |
| `get_private_files_manager(app_name=None) -> PrivateFilesManager` | Returns the cached manager for `app_name`. |
| `get_private_app_dir(app_name=None) -> Path` | `manager.get_root_dir()` |
| `create_private_app_dir(app_name=None) -> Path` | `manager.create_root_dir()` |
| `get_private_dir(subdir, app_name=None) -> Path` | `manager.get_private_dir(subdir)` |
| `create_private_dir(subdir, app_name=None) -> Path` | `manager.create_private_dir(subdir)` |
| `delete_private_dir(subdir_name, app_name) -> None` | `manager.delete_private_dir(subdir_name)` |
| `verify_private_dir(subdir_name, app_name=None) -> Path` | `manager.verify_private_dir(subdir_name)` |
| `get_private_app_file(filename, *, create_parent=False, subdir=".", app_name=None) -> Path` | `manager.get_private_file(...)` |
| `open_private_app_file(filename, mode="r", *, subdir=".", create_parent=False, app_name=None, **kwargs) -> IO` | `manager.open(...)` |

`get_private_files_manager(app_name)` is `@functools.cache`d, so repeated calls with the same `app_name`
(directly, or indirectly via any of the flat functions above) return the same manager instance and reuse
its already-resolved, already-verified paths. This means the *first* successful resolution of a given
`app_name`'s directory sticks for the lifetime of the process, even if the underlying shared root were to
change (e.g. `$HOME` changing at runtime) &mdash; construct a fresh `PrivateFilesManager` directly if you
need to bypass the cache.

## Examples

### Reading and writing binary data

```python
from private_files import open_private_app_file

with open_private_app_file("cache.bin", "wb", app_name="myapp") as f:
    f.write(b"\x00\x01\x02")

with open_private_app_file("cache.bin", "rb", app_name="myapp") as f:
    data = f.read()
```

### Nested subdirectories

```python
from private_files import PrivateFilesManager

files = PrivateFilesManager(app_name="myapp")
files.create_private_dir("sessions/2024")  # creates both levels, each mode 0700
path = files.get_private_dir("sessions/2024")
```

### Checking a directory without creating it

```python
from private_files import verify_private_dir

try:
    verify_private_dir("sessions", app_name="myapp")
except (NotADirectoryError, PermissionError) as e:
    print(f"not ready: {e}")
```

### Cleaning up

```python
from private_files import delete_private_dir

# Removes the directory and everything under it. No error if it doesn't exist.
delete_private_dir("sessions", app_name="myapp")
```

## Supported Python Versions

Python 3.10 through 3.14.

## License

MIT. See [LICENSE](LICENSE).

---

For development and release workflow documentation, see [CONTRIBUTING.md](CONTRIBUTING.md).

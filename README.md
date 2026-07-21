# private-files

[![CI](https://img.shields.io/badge/CI-passing-brightgreen.svg)](https://github.com/mckelvie-org/private-files/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v3.2.4-blue.svg)](https://pypi.org/project/private-files/3.2.4/)
[![Python versions](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13%20|%203.14-blue.svg)](https://pypi.org/project/private-files/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`private-files`: Manage secret/private files in an os-independent way.

Applications that need to persist sensitive data on a user's machine &mdash; API tokens, session cookies,
credentials, profile data &mdash; need somewhere to put it that isn't the world-readable home directory
clutter of `~/.config` or `~/.myapp`. `private-files` gives every application its own subdirectory of a
single, locked-down, user-wide private root, and enforces `0700`/`0600` permissions on directories and
files it creates so secrets are never accidentally left group- or world-readable.

## Highlights

- **A private directory per application.** Each `app_name` gets its own locked-down directory:
  `~/.private/<app_name>` on Linux/macOS, or `%LOCALAPPDATA%\<app_name>\private` on Windows (via
  [`platformdirs`](https://pypi.org/project/platformdirs/)), following the Windows convention of
  keeping everything for an app under its own folder. Neither location is synced to the cloud or
  shared across devices.
- **Permissions are enforced, not assumed.** Directories created by this package are `chmod 0700`; files
  opened for writing are `chmod 0600`. Existing app-specific directories with the wrong permissions are
  fixed automatically. On Linux/macOS, the shared `~/.private` root itself is only ever checked, never
  silently "fixed," since it may be shared with other applications.
- **Path-traversal safe.** Subdirectory and filename arguments are resolved and checked to ensure they
  stay within the intended app directory &mdash; a `subdir` or `filename` of `"../../etc/passwd"` raises
  `ValueError` instead of silently escaping the sandbox.
- **Optional passphrase encryption.** `PrivateFilesManager.open()` can transparently encrypt a file at
  rest with a passphrase (Argon2id key derivation + AES-256-GCM authenticated encryption), with normal
  read/write/seek/append/update semantics.
- **Optional atomic updates.** `PrivateFilesManager.open(..., atomic_update=True)` writes through a
  temporary file and renames it over the target only on a successful close, so the target is never left
  partially written. The returned file also gets an `abort()` method to deliberately discard a write in
  progress.
- **Drop-in `open()` replacement.** `PrivateFilesManager.open()` behaves like the builtin `open()`
  &mdash; including `@overload`-based mode-based return-type inference (`TextIO` vs `BinaryIO`) &mdash;
  but resolves the path into the private directory and creates parent directories and fixes file
  permissions automatically.
- **Fully typed**, `mypy --strict` clean, zero required dependencies beyond `platformdirs` and
  `cryptography`.

## Installation

```bash
pip install private-files
```

## Quick Start

```python
from private_files import get_private_files

files = get_private_files(app_name="myapp")

# Write a secret. Parent directories are created automatically (mode 0700),
# and the file itself ends up with mode 0600.
with files.open("api-token.txt", "w") as f:
    f.write("super-secret-token")

# Read it back later.
with files.open("api-token.txt", "r") as f:
    token = f.read()

# Find out where it lives on disk, without opening it.
print(files.get_root_dir())
# -> /home/alice/.private/myapp                    (Linux/macOS)
# -> C:\Users\alice\AppData\Local\myapp\private     (Windows)
```

`get_private_files(app_name)` returns a process-wide cached `PrivateFilesManager` for a given `app_name` --
repeated calls with the same `app_name` return the same instance, reusing its already-resolved,
already-verified paths. You can also construct a `PrivateFilesManager` directly (e.g. to bypass the
cache, or if you're only ever going to use one `app_name` and would rather hold a local reference):

```python
from private_files import PrivateFilesManager

files = PrivateFilesManager(app_name="myapp")
```

## Concepts

### Directory layout

Each `PrivateFilesManager(app_name=...)` resolves to a single directory named after `app_name` that this
package fully owns: it creates the directory if missing and actively enforces/repairs `0700` permissions
on it and any subdirectories you create within it:

- **Linux/macOS**: `~/.private/<app_name>`, a subdirectory of one shared, user-wide root. `~/.private`
  must already have (or be given) permissions `0700`; this package creates it if missing, but will
  **not** silently fix its permissions if it already exists with the wrong mode, since it may be shared
  with other applications you don't control &mdash; instead it raises `PermissionError` so you can decide
  what to do.
- **Windows**: `%LOCALAPPDATA%\<app_name>\private`, nested inside that app's own per-user local app-data
  folder, so deleting `%LOCALAPPDATA%\<app_name>` cleans up an app's private data along with the rest of
  its data.

`app_name=None` (the default) uses this library's own name, `private_files`, as `app_name` -- a peer of
every other app's directory, not a parent of them. Direct access to the underlying shared platform
directory itself (e.g. `~/.private` on Linux/macOS) is only available by constructing a `PrivateDirManager`
against it directly; see [Subdirectory managers](#subdirectory-managers).

### Subdirectories and files

Within an application's directory you can create arbitrarily nested subdirectories (`subdir="cache/v2"`)
and files within them. Every directory component created by `create_private_dir()` (or implicitly by
`create_parent=True`) gets its permissions verified and, if necessary, corrected to `0700`. Every
directory checked by `verify_private_dir()` must already be `0700`, or a `PermissionError` is raised.

All `subdir` and `filename` arguments are resolved and checked against the directory they're supposed to
be contained within; anything that would resolve outside of it (via `..`, absolute paths outside the
tree, symlinks, etc.) raises `ValueError` rather than being silently permitted.

### Subdirectory managers

`get_subdir_manager(subdir, create=True)` returns a new, independent manager scoped to a subdirectory --
it behaves exactly like a full manager rooted at that subdirectory (`get_private_dir`, `create_private_dir`,
`delete_private_dir`, `open`, etc. all work the same way, just relative to the subdirectory instead of the
original manager's root), but it will never touch anything at or above that subdirectory, including the
original manager's own root.

```python
sessions = files.get_subdir_manager("sessions")
with sessions.open("current.json", "w") as f:
    f.write('{"user": "alice"}')
```

By default (`create=True`) both the original manager's root and the subdirectory are created (with
permissions fixed/verified) if they don't already exist. Pass `create=False` to just resolve and validate
the subdirectory path without touching the filesystem at all.

This is also handy for testing code that uses `PrivateFilesManager`: since a returned sub-manager is a
self-contained `PrivateDirManager` rooted anywhere on disk, you can construct one directly against a
temporary directory in a test, instead of needing to redirect where `PrivateFilesManager` itself stores
private data.

### Passphrase encryption

`open()` accepts an optional `passphrase`. When given, the file is encrypted at rest: a key is derived
from the passphrase with Argon2id (deliberately expensive, to slow down dictionary/brute-force attacks),
and the content is sealed with AES-256-GCM authenticated encryption. Encryption/decryption happens as a
whole (in memory) rather than streamed, so every mode -- including `"r+"`, `"a"`/`"a+"`, and arbitrary
`seek()` -- works normally; there are no seek restrictions.

```python
with files.open("secret.json", "w", passphrase="correct horse battery staple") as f:
    f.write('{"token": "abc123"}')

with files.open("secret.json", "r", passphrase="correct horse battery staple") as f:
    data = f.read()
```

Reading an encrypted file with the wrong passphrase, or a plaintext file with a passphrase given, raises
`DecryptionError` (or a more specific subclass: `PassphraseRequiredError`, `NotEncryptedError`). By
default, reading an encrypted file *without* a passphrase is not blocked -- you get the raw ciphertext
bytes back, e.g. to copy or back the file up. Pass `check_encryption=True` to instead raise
`PassphraseRequiredError` up front if the file looks encrypted:

```python
from private_files import PassphraseRequiredError

try:
    files.open("secret.json", "r", check_encryption=True)
except PassphraseRequiredError:
    print("this file needs a passphrase")
```

`files.looks_encrypted("secret.json")` answers the same question directly, without opening the file or
needing a passphrase (it just peeks at the file's header). It returns `False` for files that don't exist.

### Atomic updates

`open()` accepts an optional `atomic_update`. When `True` and the file is opened for writing, the new
content is written to a temporary file (`filename` + `temp_file_extension`, default `.tmp`) and only
renamed over the target once the file is closed successfully -- the target is never left partially
written. This is fully atomic on Linux/macOS (`os.replace()` is atomic there); on Windows there's a brief
window where the target is removed before the temp file is renamed into its place.

```python
with files.open("config.json", "w", atomic_update=True) as f:
    f.write('{"setting": "value"}')
```

The returned file also has an `abort()` method: calling it marks the pending write to be discarded
instead of committed the next time the file is closed. This happens automatically if a `with` block exits
because of an exception, or if the file is garbage-collected without ever having been explicitly closed --
in both cases the target is left untouched. You can also call `abort()` yourself, e.g. to bail out of a
write you've decided not to keep, without needing to raise an exception to trigger it:

```python
with files.open("config.json", "w", atomic_update=True) as f:
    f.write(build_config())
    if not looks_valid(f.getvalue()):
        f.abort()  # target is left untouched
```

Without `atomic_update`, `abort()` is still available (whenever a `passphrase` is given, or when
`atomic_update=True` was passed but had no effect -- see below), but the target may already have been
truncated as a side effect of opening it in `"w"`/`"x"` mode, so aborting leaves an empty file rather than
the original content; update/append modes (`"r+"`, `"a"`, `"a+"`) are never touched until a successful
close, so aborting those does leave the original content intact. `atomic_update` avoids this asymmetry
entirely, since the target isn't touched at all until a fully-written temp file is ready to replace it.

`abort()` is a harmless no-op on a read-only open, since there's nothing pending to discard.

When `atomic_update=True` is passed explicitly, `open()`'s return type is `AbortableTextIO` /
`AbortableBinaryIO` (both exported from the package) instead of plain `TextIO`/`BinaryIO`, so `abort()`
is available on the result without a cast. In the `passphrase`-only case above (no `atomic_update`),
`abort()` is present at runtime but the static return type stays plain `TextIO`/`BinaryIO`.

## API Reference

### `get_private_files(app_name=None) -> PrivateFilesManager`

The package's single entry point. Returns a cached `PrivateFilesManager` for the given `app_name`
(`DEFAULT_APP_NAME` if `app_name` is `None`) -- repeated calls with the same `app_name` return the same
instance.

### `PrivateFilesManager`

```python
class PrivateFilesManager:
    def __init__(self, app_name: str | None = None): ...
```

An object bound to a single `app_name` (`DEFAULT_APP_NAME` if `None`) that resolves and caches its
directory paths across calls.

| Method | Description |
| --- | --- |
| `get_root_dir() -> Path` | This manager's app-specific directory. See [Directory layout](#directory-layout). Computed, not created. |
| `create_root_dir() -> Path` | Create the app-specific directory (and any parent directories needed to reach it), fixing permissions at every level. |
| `get_private_dir(subdir) -> Path` | Resolve `subdir` under the app directory. Does not create anything. |
| `create_private_dir(subdir) -> Path` | Create `subdir` (and every intermediate component) under the app directory, mode `0700`. |
| `get_subdir_manager(subdir, create=True) -> PrivateDirManager` | Return a new, independent manager scoped to `subdir`, which never touches anything at or above it. See [Subdirectory managers](#subdirectory-managers). |
| `delete_private_dir(subdir) -> None` | Recursively delete `subdir`. Raises `NotADirectoryError` if it doesn't exist, or `ValueError` if `subdir` resolves to this manager's own root directory. |
| `delete_app_data() -> None` | Completely delete this app's private data directory itself (and everything in it), if it exists. Unlike `delete_private_dir`, this deletes the root directory itself -- for a full uninstall/reset, not routine cleanup. No-op if it doesn't already exist. Only on `PrivateFilesManager`, not `PrivateDirManager`. |
| `verify_private_dir(subdir) -> Path` | Raise `NotADirectoryError`/`PermissionError` unless `subdir` (and everything above it, up to the top of the directory tree this manager owns) exists with mode `0700`. |
| `get_private_file(filename, *, create_parent=False, subdir=".") -> Path` | Resolve the full path to a file. Verifies (or creates, if `create_parent=True`) its parent directory. The file itself is never created. |
| `looks_encrypted(filename, *, subdir=".") -> bool` | Peek at a file's header to see if it looks passphrase-encrypted, without needing a passphrase. `False` if the file doesn't exist. |
| `open(filename, mode="r", *, subdir=".", create_parent=False, passphrase=None, check_encryption=False, atomic_update=False, temp_file_extension=".tmp", **kwargs) -> IO` | Like builtin `open()`, but resolved into the app directory. Parent directories are auto-created for write/append/exclusive-create modes (or when `create_parent=True`); files opened for writing get mode `0600`. See [Passphrase encryption](#passphrase-encryption) for `passphrase`/`check_encryption`, and [Atomic updates](#atomic-updates) for `atomic_update`/`temp_file_extension` and the returned object's `abort()` method. |

`subdir="."` refers to the app directory itself. All `subdir`/`filename` parameters accept `str | Path`.

### Exceptions

| Exception | Raised when |
| --- | --- |
| `DecryptionError` (`ValueError`) | Base class for all decryption failures: wrong passphrase, corrupted/tampered/truncated data, or an unsupported format version. |
| `NotEncryptedError` (`DecryptionError`) | A `passphrase` was given, but the file doesn't have this package's encrypted-file header. |
| `PassphraseRequiredError` (`DecryptionError`) | `check_encryption=True` and the file looks encrypted, but no `passphrase` was given. |

## Examples

### Reading and writing binary data

```python
from private_files import get_private_files

files = get_private_files(app_name="myapp")

with files.open("cache.bin", "wb") as f:
    f.write(b"\x00\x01\x02")

with files.open("cache.bin", "rb") as f:
    data = f.read()
```

### Nested subdirectories

```python
files.create_private_dir("sessions/2024")  # creates both levels, each mode 0700
path = files.get_private_dir("sessions/2024")
```

### Checking a directory without creating it

```python
try:
    files.verify_private_dir("sessions")
except (NotADirectoryError, PermissionError) as e:
    print(f"not ready: {e}")
```

### Cleaning up

```python
# Removes the directory and everything under it. No error if it doesn't exist.
files.delete_private_dir("sessions")
```

## Supported Python Versions

Python 3.10 through 3.14.

## License

MIT. See [LICENSE](LICENSE).

---

For development and release workflow documentation, see [CONTRIBUTING.md](CONTRIBUTING.md).

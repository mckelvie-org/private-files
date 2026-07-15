# private-files

[![CI](https://img.shields.io/badge/CI-passing-brightgreen.svg)](https://github.com/mckelvie-org/private-files/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v2.1.0rc1-blue.svg)](https://test.pypi.org/project/private-files/2.1.0rc1/)
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
- **Optional passphrase encryption.** `PrivateFilesManager.open()` can transparently encrypt a file at
  rest with a passphrase (Argon2id key derivation + AES-256-GCM authenticated encryption), with normal
  read/write/seek/append/update semantics.
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
from private_files import private_files

files = private_files(app_name="myapp")

# Write a secret. Parent directories are created automatically (mode 0700),
# and the file itself ends up with mode 0600.
with files.open("api-token.txt", "w") as f:
    f.write("super-secret-token")

# Read it back later.
with files.open("api-token.txt", "r") as f:
    token = f.read()

# Find out where it lives on disk, without opening it.
print(files.get_root_dir())
# -> /home/alice/.private/myapp   (Linux/macOS)
# -> C:\Users\alice\AppData\Local\myapp\myapp   (Windows)
```

`private_files(app_name)` returns a process-wide cached `PrivateFilesManager` for a given `app_name` --
repeated calls with the same `app_name` return the same instance, reusing its already-resolved,
already-verified paths. You can also construct a `PrivateFilesManager` directly (e.g. to bypass the
cache, or if you're only ever going to use one `app_name` and would rather hold a local reference):

```python
from private_files import PrivateFilesManager

files = PrivateFilesManager(app_name="myapp")
```

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

## API Reference

### `private_files(app_name=None) -> PrivateFilesManager`

The package's single entry point. Returns a cached `PrivateFilesManager` for the given `app_name` (or the
shared root itself, if `app_name` is `None`) -- repeated calls with the same `app_name` return the same
instance.

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
| `looks_encrypted(filename, *, subdir=".") -> bool` | Peek at a file's header to see if it looks passphrase-encrypted, without needing a passphrase. `False` if the file doesn't exist. |
| `open(filename, mode="r", *, subdir=".", create_parent=False, passphrase=None, check_encryption=False, **kwargs) -> IO` | Like builtin `open()`, but resolved into the app directory. Parent directories are auto-created for write/append/exclusive-create modes (or when `create_parent=True`); files opened for writing get mode `0600`. See [Passphrase encryption](#passphrase-encryption) for `passphrase`/`check_encryption`. |

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
from private_files import private_files

files = private_files(app_name="myapp")

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

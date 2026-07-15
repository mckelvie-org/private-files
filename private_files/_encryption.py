"""
Passphrase-based encryption support for PrivateFilesManager.open().

File format: [7-byte magic "PRVFILE"][1-byte version][16-byte salt][12-byte nonce][ciphertext][16-byte tag]
The magic+version header is self-describing and lets a file be recognized as (this library's)
encrypted format without needing a passphrase -- see looks_encrypted().
Key derivation: Argon2id (memory-hard, discourages dictionary/brute-force attacks).
Cipher: AES-256-GCM (authenticated encryption -- detects tampering/corruption).

Permission handling (umask override, chmod 0600) is intentionally NOT duplicated here --
the real underlying file is always opened through the caller-supplied `opener`, which is
expected to be PrivateFilesManager's own standard (non-encrypted) file-opening logic. This
module only ever deals with the plaintext/ciphertext translation.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any, BinaryIO, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

MAGIC: bytes = b"PRVFILE"
FORMAT_VERSION: bytes = b"\x01"
HEADER_PREFIX: bytes = MAGIC + FORMAT_VERSION
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32  # AES-256

# Argon2id cost parameters -- deliberately expensive to slow down dictionary/brute-force attacks.
# Tuned for a local, single-user, occasional-use context (not a high-throughput server).
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST_KIB = 65536  # 64 MiB
ARGON2_PARALLELISM = 4

Opener = Callable[..., IO[Any]]


class DecryptionError(ValueError):
    """Raised when encrypted private-file content cannot be decrypted: wrong passphrase, or
    corrupted/tampered/truncated data."""


class NotEncryptedError(DecryptionError):
    """Raised when a passphrase was given to open a file that does not have this library's
    encrypted-file header (magic+version)."""


class PassphraseRequiredError(DecryptionError):
    """Raised (only when check_encryption=True) when a file has this library's encrypted-file
    header, but no passphrase was supplied to open it."""


def _coerce_passphrase(passphrase: str | bytes) -> bytes:
    return passphrase.encode("utf-8") if isinstance(passphrase, str) else passphrase


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    kdf = Argon2id(
        salt=salt,
        length=KEY_SIZE,
        iterations=ARGON2_TIME_COST,
        lanes=ARGON2_PARALLELISM,
        memory_cost=ARGON2_MEMORY_COST_KIB,
    )
    return kdf.derive(passphrase)


def looks_encrypted(path: str | Path) -> bool:
    """Peek at path's header to see if it looks like a private_files encrypted file, without
    needing (or attempting to verify) a passphrase. Returns False if path does not exist."""
    try:
        with Path(path).open("rb") as f:
            prefix = f.read(len(MAGIC))
    except FileNotFoundError:
        return False
    return prefix.startswith(MAGIC)


def encrypt(plaintext: bytes, passphrase: str | bytes, associated_data: bytes) -> bytes:
    """Encrypt plaintext with a passphrase, binding the ciphertext to associated_data
    (e.g. the target file path, to detect a ciphertext being moved/swapped to a different path)."""
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(_coerce_passphrase(passphrase), salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    return HEADER_PREFIX + salt + nonce + ciphertext


def decrypt(blob: bytes, passphrase: str | bytes, associated_data: bytes) -> bytes:
    """Decrypt a blob produced by encrypt(). Raises NotEncryptedError if blob doesn't have this
    library's header at all, or DecryptionError on an unsupported version, a wrong passphrase,
    corrupted/truncated data, or a mismatched associated_data binding."""
    if not blob.startswith(MAGIC):
        raise NotEncryptedError("Data does not have the private_files encrypted-file header; it does not appear to be encrypted.")
    header_size = len(HEADER_PREFIX) + SALT_SIZE + NONCE_SIZE
    if len(blob) < header_size:
        raise DecryptionError("Encrypted data is truncated or not in the expected format.")
    version = blob[len(MAGIC):len(HEADER_PREFIX)]
    if version != FORMAT_VERSION:
        raise DecryptionError(f"Unsupported encrypted file format version: {version!r}.")
    salt = blob[len(HEADER_PREFIX):len(HEADER_PREFIX) + SALT_SIZE]
    nonce = blob[len(HEADER_PREFIX) + SALT_SIZE:header_size]
    ciphertext = blob[header_size:]
    key = _derive_key(_coerce_passphrase(passphrase), salt)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, associated_data)
    except InvalidTag as e:
        raise DecryptionError("Incorrect passphrase, or the encrypted data is corrupted or has been tampered with.") from e


def _binary_open_mode(mode: str, exists: bool) -> str:
    """Translate a caller-supplied open() mode into the binary mode used for the real,
    on-disk (encrypted) file, given whether it currently exists.

    Append needs both read (to seed existing content) and write (to flush at close) access to
    the real file, and can't use real append mode -- O_APPEND would force every write to EOF,
    breaking the seek(0)-and-overwrite used to flush the re-encrypted whole-file blob.
    """
    if "a" in mode:
        return "rb+" if exists else "wb+"
    plus = "+" in mode
    if "x" in mode:
        return "xb+" if plus else "xb"
    if "w" in mode:
        return "wb+" if plus else "wb"
    return "rb+" if plus else "rb"


class EncryptedFile(io.BytesIO):
    """A seekable, in-memory binary buffer that transparently decrypts existing content on
    construction and, if opened in a writing mode, encrypts and writes the final buffer
    contents back to the real file on close().

    Since encryption/decryption is performed once as a whole (not streamed chunk-by-chunk),
    normal seek()/read()/write() semantics work everywhere -- there are no seek restrictions.
    """

    def __init__(self, path: str | Path, mode: str, passphrase: str | bytes, opener: Opener, **kwargs: Any) -> None:
        path = Path(path)
        self._real_file: IO[bytes] | None = None
        self._writes = any(m in mode for m in "wax+")
        self._passphrase = passphrase
        self._associated_data = str(path.resolve()).encode("utf-8")

        binary_mode = _binary_open_mode(mode, path.is_file())
        real_file = cast(IO[bytes], opener(path, binary_mode, **kwargs))
        try:
            initial = b""
            if binary_mode[0] == "r":
                initial = decrypt(real_file.read(), passphrase, self._associated_data)
            if self._writes:
                self._real_file = real_file
            else:
                real_file.close()
        except BaseException:
            real_file.close()
            raise

        super().__init__(initial)
        if "a" in mode:
            self.seek(0, io.SEEK_END)

    def close(self) -> None:
        if not self.closed:
            real_file = self._real_file
            if real_file is not None:
                blob = encrypt(self.getvalue(), self._passphrase, self._associated_data)
                real_file.seek(0)
                real_file.write(blob)
                real_file.truncate()
                real_file.close()
        super().close()


def open_encrypted(path: str | Path, mode: str, passphrase: str | bytes, opener: Opener, **kwargs: Any) -> IO[Any]:
    """Open path with transparent passphrase encryption, honoring the same mode semantics
    as builtin open() (text vs binary, read/write/append/exclusive/update). The real file is
    always accessed through opener(path, binary_mode, **kwargs), so permission handling lives
    entirely in the caller (PrivateFilesManager's standard open logic), not here."""
    binary = "b" in mode
    encoding = kwargs.pop("encoding", None)
    errors = kwargs.pop("errors", None)
    newline = kwargs.pop("newline", None)
    raw = EncryptedFile(path, mode, passphrase, opener, **kwargs)
    if binary:
        return raw
    return io.TextIOWrapper(cast(BinaryIO, raw), encoding=encoding, errors=errors, newline=newline)

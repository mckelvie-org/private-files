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

import contextlib
import io
import os
from abc import abstractmethod
from pathlib import Path
from types import TracebackType
from typing import IO, Any, BinaryIO, Literal, TextIO, cast, overload

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from .util import OpenBinaryMode, Opener, OpenTextMode


class AbortableTextIO(TextIO):
    """A TextIO that also supports abort() (see WrappedFile.abort()). Returned by open()/
       open_wrapped() instead of plain TextIO whenever atomic_update=True is passed explicitly,
       so callers get abort() in the static type without a cast."""

    @abstractmethod
    def abort(self) -> None: ...


class AbortableBinaryIO(BinaryIO):
    """A BinaryIO that also supports abort() (see WrappedFile.abort()). Returned by open()/
       open_wrapped() instead of plain BinaryIO whenever atomic_update=True is passed explicitly,
       so callers get abort() in the static type without a cast."""

    @abstractmethod
    def abort(self) -> None: ...


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


def _load_initial(raw: bytes, passphrase: bytes | None, check_encryption: bool, associated_data: bytes) -> bytes:
    """Interpret raw bytes just read from the real file as the initial buffer contents: decrypt
       if passphrase is given, otherwise return as-is -- unless check_encryption requests that an
       encrypted-looking file with no passphrase raise instead of silently returning ciphertext.
       Operates on bytes already read into memory, so no extra file read (and no TOCTOU race
       against a separate peek-then-open) is needed to honor check_encryption.
    """
    if passphrase is None:
        if check_encryption and raw.startswith(MAGIC):
            raise PassphraseRequiredError(
                "This file appears to be passphrase-encrypted; pass passphrase= to open() to read it."
            )
        return raw
    return decrypt(raw, passphrase, associated_data)


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


class WrappedFile(io.BytesIO, AbortableBinaryIO):
    """A seekable, in-memory binary buffer that wraps access to a file and provides
       additional semantics including:

       - Optional transparent encryption/decryption of the file's contents using a passphrase.
       - Optional atomic update semantics (write to a temporary file and rename on close).

       Since encryption/decryption (and, when used, the final atomic write) is performed once
       as a whole (not streamed chunk-by-chunk), normal seek()/read()/write() semantics work
       everywhere -- there are no seek restrictions.

       Calling abort() marks the pending write to be discarded instead of committed the next
       time the file is closed -- whether that close happens explicitly, when a with block exits
       because of an exception, or when the file is garbage-collected without ever having been
       closed. Callers can also call abort() themselves to deliberately discard a write in
       progress without having to raise an exception to trigger it.
    """

    _real_file: IO[bytes] | None = None
    """The underlying open real file handle used for in-place writeback at close(), opened in
       binary mode. None if the file was opened read-only, or if atomic_update is in effect
       (atomic_update always writes through a fresh handle to the temp file at close() instead)."""

    _writes: bool = False
    """True if the file was opened in a writing mode (w, a, x, +)."""

    _passphrase: bytes | None = None
    """The passphrase used for encryption/decryption. None if the file is not encrypted."""

    _associated_data: bytes = b""
    """The associated data used for encryption/decryption. By default this is empty."""

    _atomic_update: bool = False
    """True if atomic_update is in effect (implies _writes)."""

    _aborted: bool = False
    """True if abort() has been called: the next close() discards the pending write instead of
       committing it."""

    _path: Path | None = None
    """The real target path. Set as soon as it's known, before anything that could raise."""

    _temp_path: Path | None = None
    """The temporary file path written at close() and renamed over _path, when _atomic_update."""

    _opener: Opener | None = None
    """The opener callable, retained so close() can open a fresh handle to the temp file
       when _atomic_update."""

    _open_args: tuple[Any, ...] = ()
    _open_kwargs: dict[str, Any] = {}
    """The positional/keyword arguments originally passed through to opener(), replayed at
       close() to open the temp file when _atomic_update."""

    def __init__(
                self,
                path: str | Path,
                mode: str,
                opener: Opener,
                *args: Any,
                passphrase: str | bytes | None = None,
                check_encryption: bool = False,
                atomic_update: bool = False,
                temp_file_extension: str = ".tmp",
                associated_data: str | bytes | None = None,
                **kwargs: Any
            ) -> None:
        path = Path(path)
        self._path = path
        if isinstance(passphrase, str):
            passphrase = passphrase.encode("utf-8")
        if isinstance(associated_data, str):
            associated_data = associated_data.encode("utf-8")
        if associated_data is None:
            associated_data = b""
        self._writes = any(m in mode for m in "wax+")
        self._passphrase = passphrase
        self._associated_data = associated_data
        self._opener = opener
        self._open_args = args
        self._open_kwargs = kwargs

        exists = path.is_file()
        binary_mode = _binary_open_mode(mode, exists)
        needs_read = binary_mode[0] == "r"
        initial = b""

        if atomic_update and self._writes:
            # Never touch the target here -- for "w"/"x" this means it isn't created/truncated
            # until close() succeeds; for "r+"/"a"/"a+" this means we only ever read it, through
            # a plain read-only handle, and write the final result to a sibling temp file instead.
            # _atomic_update/_temp_path are deliberately not set until everything below that could
            # raise (the "x" exclusivity check, the read, decryption) has already succeeded: if
            # __init__ raises, this object is abandoned and garbage-collected, which triggers
            # io.IOBase's implicit close() -- and that must NOT attempt an atomic replace of the
            # target using an empty/uninitialized buffer.
            if "x" in mode and path.exists():
                raise FileExistsError(f"File exists: {path}")
            temp_path = path.with_name(path.name + temp_file_extension)
            if needs_read:
                real_file = cast(IO[bytes], opener(path, "rb", *args, **kwargs))
                try:
                    raw = real_file.read()
                finally:
                    real_file.close()
                initial = _load_initial(raw, passphrase, check_encryption, self._associated_data)
            self._atomic_update = True
            self._temp_path = temp_path
        else:
            real_file = cast(IO[bytes], opener(path, binary_mode, *args, **kwargs))
            try:
                if needs_read:
                    raw = real_file.read()
                    initial = _load_initial(raw, passphrase, check_encryption, self._associated_data)
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

    def abort(self) -> None:
        """Mark this file as aborted: the next close() (however triggered -- explicitly, via a
           with block's __exit__, or via __del__) discards the pending write instead of
           committing it. For atomic_update, the temp file (if any was written) is removed
           without being renamed over the target; otherwise, the real file handle is closed
           without writing the buffered content back, leaving the target as it was on open.
           Idempotent, and safe to call even if nothing was ever written.
        """
        self._aborted = True

    def close(self) -> None:
        # Mirrors the stdlib file-object contract: even if flushing the final content fails
        # (e.g. disk full, os.replace() error), the file ends up closed rather than left open
        # to retry -- and doomed to retry again, silently, when __del__ calls close() again.
        if self.closed:
            return
        try:
            if self._atomic_update:
                if self._aborted:
                    assert self._temp_path is not None
                    with contextlib.suppress(OSError):
                        os.remove(self._temp_path)
                else:
                    plaintext = self.getvalue()
                    blob = plaintext if self._passphrase is None else encrypt(plaintext, self._passphrase, self._associated_data)
                    assert self._opener is not None and self._temp_path is not None and self._path is not None
                    temp_file = cast(IO[bytes], self._opener(self._temp_path, "wb", *self._open_args, **self._open_kwargs))
                    try:
                        temp_file.write(blob)
                    finally:
                        temp_file.close()
                    try:
                        os.replace(self._temp_path, self._path)
                    except BaseException:
                        with contextlib.suppress(OSError):
                            os.remove(self._temp_path)
                        raise
            else:
                real_file = self._real_file
                if real_file is not None:
                    if not self._aborted:
                        plaintext = self.getvalue()
                        blob = plaintext if self._passphrase is None else encrypt(plaintext, self._passphrase, self._associated_data)
                        real_file.seek(0)
                        real_file.write(blob)
                        real_file.truncate()
                    real_file.close()
        finally:
            super().close()

    def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc_value: BaseException | None,
                traceback: TracebackType | None,
            ) -> None:
        # Unlike the inherited IOBase.__exit__ (which always just calls close()), an exception
        # propagating out of the with block means the pending write should be discarded, not
        # committed -- atomic_update's whole point is that the target only ever reflects a fully
        # written result.
        if exc_type is not None:
            self.abort()
        self.close()

    def __del__(self) -> None:
        # Reaching finalization while still open means close() (and, for a with block, __exit__)
        # was never reached -- the same "pending write should not be committed" situation as an
        # exceptional __exit__. super().__del__() (io.IOBase) still emits the usual "unclosed
        # file" ResourceWarning and calls close(), which -- now that _aborted is set -- discards
        # rather than commits.
        if not self.closed:
            self.abort()
        super().__del__()


class _AbortableTextIOWrapper(io.TextIOWrapper, AbortableTextIO):
    """A TextIOWrapper that exposes its underlying WrappedFile buffer's abort() (see
       WrappedFile.abort()) to callers, and also forwards it automatically on an exceptional
       __exit__ or on GC without an explicit close() -- otherwise, text-mode opens (the default)
       would silently bypass WrappedFile's discard-on-failure behavior, since TextIOWrapper's own
       inherited __exit__/__del__ just call close() unconditionally, and abort() would not be
       reachable at all through the object callers actually get back from open().
    """

    def abort(self) -> None:
        """See WrappedFile.abort(): marks the pending write to be discarded instead of
           committed the next time this file is closed."""
        cast(WrappedFile, self.buffer).abort()

    def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc_value: BaseException | None,
                traceback: TracebackType | None,
            ) -> None:
        if exc_type is not None:
            self.abort()
        self.close()

    def __del__(self) -> None:
        if not self.closed:
            self.abort()
        super().__del__()


@overload
def open_wrapped(
        path: str | Path,
        mode: OpenTextMode,
        opener: Opener,
        *args: Any,
        passphrase: str | bytes | None = None,
        check_encryption: bool = False,
        atomic_update: Literal[True],
        temp_file_extension: str = ".tmp",
        **kwargs: Any
    ) -> AbortableTextIO: ...

@overload
def open_wrapped(
        path: str | Path,
        mode: OpenBinaryMode,
        opener: Opener,
        *args: Any,
        passphrase: str | bytes | None = None,
        check_encryption: bool = False,
        atomic_update: Literal[True],
        temp_file_extension: str = ".tmp",
        **kwargs: Any
    ) -> AbortableBinaryIO: ...

@overload
def open_wrapped(
        path: str | Path,
        mode: OpenTextMode,
        opener: Opener,
        *args: Any,
        passphrase: str | bytes | None = None,
        check_encryption: bool = False,
        atomic_update: bool = False,
        temp_file_extension: str = ".tmp",
        **kwargs: Any
    ) -> TextIO: ...

@overload
def open_wrapped(
        path: str | Path,
        mode: OpenBinaryMode,
        opener: Opener,
        *args: Any,
        passphrase: str | bytes | None = None,
        check_encryption: bool = False,
        atomic_update: bool = False,
        temp_file_extension: str = ".tmp",
        **kwargs: Any
    ) -> BinaryIO: ...

@overload
def open_wrapped(
        path: str | Path,
        mode: str,
        opener: Opener,
        *args: Any,
        passphrase: str | bytes | None = None,
        check_encryption: bool = False,
        atomic_update: bool = False,
        temp_file_extension: str = ".tmp",
        **kwargs: Any
    ) -> IO[Any]: ...

def open_wrapped(
            path: str | Path,
            mode: str,
            opener: Opener,
            *args: Any,
            passphrase: str | bytes | None = None,
            check_encryption: bool = False,
            atomic_update: bool = False,
            temp_file_extension: str = ".tmp",
            **kwargs: Any
        ) -> IO[Any]:
    """Open path with optional transparent passphrase encryption and/or atomic-update semantics,
       honoring the same mode semantics as builtin open() (text vs binary, read/write/append/
       exclusive/update). The real file is always accessed through opener(path, binary_mode,
       **kwargs), so permission handling lives entirely in the caller (PrivateFilesManager's
       standard open logic), not here.

       check_encryption only matters when passphrase is not given and the mode reads existing
       content: it is checked against the bytes already read into memory for that purpose, so it
       costs nothing extra and there's no race between a separate peek and the real read.
    """
    binary = "b" in mode
    encoding = kwargs.pop("encoding", None)
    errors = kwargs.pop("errors", None)
    newline = kwargs.pop("newline", None)
    raw = WrappedFile(
            path,
            mode,
            opener,
            *args,
            passphrase=passphrase,
            check_encryption=check_encryption,
            atomic_update=atomic_update,
            temp_file_extension=temp_file_extension,
            **kwargs)
    if binary:
        return raw
    return _AbortableTextIOWrapper(cast(BinaryIO, raw), encoding=encoding, errors=errors, newline=newline)

"""
Utility functions and type definitions
"""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path
from typing import IO, Any, BinaryIO, Final, Literal, Protocol, TextIO, TypeAlias, overload

from platformdirs import user_data_dir

__all__ =  [
    "OpenTextMode",
    "OpenBinaryMode",
    "UNIX_PRIVATE_DIR_ROOT_PATH",
    "Opener",
    "get_shared_private_dir",
    "create_shared_private_dir",
]

UNIX_PRIVATE_DIR_ROOT_PATH: Final[Path] = Path("~/.private")

OpenTextMode: TypeAlias = Literal[
    "r", "rt", "r+", "r+t", "rt+",
    "w", "wt", "w+", "w+t", "wt+",
    "a", "at", "a+", "a+t", "at+",
    "x", "xt", "x+", "x+t", "xt+",
]
"""Mode strings for open() calls that produce a TextIO."""

OpenBinaryMode: TypeAlias = Literal[
    "rb", "r+b", "rb+",
    "wb", "w+b", "wb+",
    "ab", "a+b", "ab+",
    "xb", "x+b", "xb+",
]
"""Mode strings for open() calls that produce a BinaryIO."""

"""
def open(
    file: FileDescriptorOrPath,
    mode: OpenTextMode = "r",
    buffering: int = -1,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
    closefd: bool = True,
    opener: _Opener | None = None,
) -> TextIOWrapper: ...
"""
class Opener(Protocol):
    """A callable that is compatible with open()"""
    @overload
    def __call__(
            self,
            file: str | Path,
            mode: OpenTextMode,
            *args: Any,
            **kwargs: Any
        ) -> TextIO: ...

    @overload
    def __call__(
            self,
            file: str | Path,
            mode: OpenBinaryMode,
            *args: Any,
            **kwargs: Any
        ) -> BinaryIO: ...

    @overload
    def __call__(
            self,
            file: str | Path,
            mode: str,
            *args: Any,
            **kwargs: Any
        ) -> IO[Any]: ...


@cache
def _get_shared_private_dir() -> Path:   # hide the @cache so that it does not screw up type hinthing for the public function.
    if sys.platform == "win32":
        # On Windows, use a well-known subdir of the non-roaming app data directory,
        # which is not backed up to the cloud and is not shared across devices.
        return Path(user_data_dir("private_files", roaming=False)).resolve()
    else:
        # On Linux and MacOS, use ~/.private, which the user can choose to encrypt or protect as needed
        return UNIX_PRIVATE_DIR_ROOT_PATH.expanduser().resolve()

def get_shared_private_dir() -> Path:
    """Get the name of the shared user-wide private root directory for storing sensitive data like authentication tokens.
    On linux and macos, this will be ~/.private, which the user can choose to encrypt or protect as needed.
    On Windows, this will be the non-roaming app data directory.

    Does not create the directory or guarantee any particular permissions, so the returned directory
    may not be safe for storing sensitive data until create_shared_private_dir has been called.
    """
    return _get_shared_private_dir()

@cache
def _create_shared_private_dir() -> Path:   # hide the @cache so that it does not screw up type hinting for the public function.
    private_dir = get_shared_private_dir()

    # create the ~/.private parent directory (or equivalent windows dir) with mode 0700 if necessary,
    # and ensure permissions are correct.
    old_umask = os.umask(0o077)
    try:
        os.makedirs(private_dir, mode=0o700, exist_ok=True)
    finally:
        os.umask(old_umask)
    if not private_dir.is_dir():
        raise NotADirectoryError(f"Expected {str(private_dir)!r} to be a directory, but it is not.")
    if sys.platform != "win32":
        current_mode = private_dir.stat().st_mode & 0o777
        if current_mode != 0o700:
            # For the shared ~/.private directory, we do not automatically fix the permissions, since it may be shared with
            # other applications.
            # But we do require that it is locked down.
            raise PermissionError(
                f"Expected {str(private_dir)!r} to have permissions 0700, but it has permissions {current_mode:04o}. "
                "Please set the permissions to 0700 to protect your sensitive data."
            )
    return private_dir

def create_shared_private_dir() -> Path:
    """Create and return the shared user-wide private root directory for storing sensitive data
    like authentication tokens, if it does not already exist. On linux and macos, this will be
    ~/.private, which the user can choose to encrypt or protect as needed.
    On Windows, this will be the non-roaming app data directory.

    If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.
    """
    return _create_shared_private_dir()


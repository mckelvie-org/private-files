"""
Utility functions and type definitions
"""

from __future__ import annotations

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
]

UNIX_PRIVATE_DIR_ROOT_PATH: Final[Path] = Path("~/.private")


@cache
def _get_base_data_dir() -> Path:
    """The per-platform base directory beneath which PrivateFilesManager resolves an
       application's private directory. This is an internal implementation detail, not a
       "shared private directory" in its own right -- see PrivateFilesManager.__init__.
    """
    if sys.platform == "win32":
        # The raw per-user local app-data root (no appname component) -- not itself a private
        # location; every app on the machine shares it.
        return Path(user_data_dir(None, roaming=False)).resolve()
    else:
        # On Linux and MacOS, ~/.private is itself the shared private root, which the user can
        # choose to encrypt or protect as needed.
        return UNIX_PRIVATE_DIR_ROOT_PATH.expanduser().resolve()

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



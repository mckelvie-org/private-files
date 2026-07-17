"""
Support for management of semnsitiive user-wide application files such as authentication tokens and profile data.
"""

from __future__ import annotations

from .private_files_manager import PrivateFilesManager, get_private_files
from .util import UNIX_PRIVATE_DIR_ROOT_PATH, OpenBinaryMode, Opener, OpenTextMode, create_shared_private_dir, get_shared_private_dir
from .wrapper import (
    AbortableBinaryIO,
    AbortableTextIO,
    DecryptionError,
    NotEncryptedError,
    PassphraseRequiredError,
    looks_encrypted,
    open_wrapped,
)

__all__ =  [
    "OpenTextMode",
    "OpenBinaryMode",
    "UNIX_PRIVATE_DIR_ROOT_PATH",
    "Opener",
    "get_shared_private_dir",
    "create_shared_private_dir",
    "AbortableTextIO",
    "AbortableBinaryIO",
    "DecryptionError",
    "NotEncryptedError",
    "PassphraseRequiredError",
    "PrivateFilesManager",
    "get_private_files",
    "looks_encrypted",
    "open_wrapped",
]


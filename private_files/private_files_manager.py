"""
PrivateDirManager and PrivateFilesManager classes
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import cache
from pathlib import Path
from typing import IO, Any, BinaryIO, Literal, TextIO, overload

from .util import OpenBinaryMode, OpenTextMode, get_shared_private_dir
from .wrapper import (
    AbortableBinaryIO,
    AbortableTextIO,
    NotEncryptedError,
    PassphraseRequiredError,
    looks_encrypted,
    open_wrapped,
)

__all__ =  [
    "NotEncryptedError",
    "PassphraseRequiredError",
    "PrivateFilesManager",
    "get_private_files",
]

def _get_partial_dirs(dir_path: Path | str, stop_dir: Path | str | None = None) -> list[Path]:
    """Get a list that represents the set of all directories paths that are equal to dir_path or
       one of its parents, but that are not equal to stop_dir or any of its parents.
       
       dir_path is the directory to process. If a relative path, it is resolved relative
       to the current working directory.
       
       If stop_dir is relative, it is relative to dir_path. It may contain "..".
       The resolved stop_dir must be equal to or a parent of dir_path, or ValueError is raised.
       If stop_dir is None, the list will include all parent directories up to the anchor.
       The list is returned in order from the anchor to dir_path, which is the order in which
       directories should be created to ensure that the parent directories exist before creating
       the child directories. The list is empty if dir_path is equal to stop_dir.
    """
    dir_path = Path(dir_path).resolve()
    if stop_dir is not None:
        stop_dir = (dir_path / Path(stop_dir)).resolve()
        if not dir_path.is_relative_to(stop_dir):
            raise ValueError(f"stop_dir {str(stop_dir)!r} is not a parent of dir_path {str(dir_path)!r}.")
    result: list[Path] = []
    current_dir = dir_path
    while current_dir != stop_dir:
        assert stop_dir is None or current_dir.is_relative_to(stop_dir)
        result.append(current_dir)
        next_dir = current_dir.parent
        if current_dir == next_dir:
            # We have reached the anchor (root) directory, so we stop here.
            break
        current_dir = next_dir
    result.reverse()
    return result

def _mkdir_private(dir_path: Path | str) -> None:
    """Create a directory with permissions 0700. Does not create parent directories.
        Does not modify the permissions of existing directories. Raises an exception
        if the directory exists.
    """
    dir_path = Path(dir_path).resolve()
    old_umask = os.umask(0o077)
    try:
        dir_path.mkdir(mode=0o700)
    finally:
        os.umask(old_umask)
        
def _fix_dir_perms(dir_path: Path | str) -> None:
    """Fix the permissions of an existing directory to 0700. Does not create the directory.
        Raises an exception if the directory does not exist or is not a directory.
    """
    dir_path = Path(dir_path).resolve()
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Expected {str(dir_path)!r} to be a directory, but it is not.")
    current_mode = dir_path.stat().st_mode & 0o777
    if current_mode != 0o700:
        dir_path.chmod(0o700)

def _verify_dir_perms(dir_path: Path | str) -> None:
    """Verify the permissions of an existing directory are 0700. Does not create the directory.
        Raises an exception if the directory does not exist or is not a directory.
    """
    dir_path = Path(dir_path).resolve()
    if not dir_path.exists():
        raise FileNotFoundError(f"Expected directory {str(dir_path)!r} to exist, but it does not.")
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Expected {str(dir_path)!r} to be a directory, but it is not.")
    current_mode = dir_path.stat().st_mode & 0o777
    if current_mode != 0o700:
        raise PermissionError(
            f"Expected {str(dir_path)!r} to have permissions 0700, but it has permissions {current_mode:04o}. "
            "Please set the permissions to 0700 to protect your sensitive data."
        )

class PrivateDirManager:
    """A class for managing private files and directories contained within a private root directory."""

    _dir_path: Path
    """The resolved directory path managed by this object. All paths managed by this class are relative to
       and equal to or contained within this directory. This path may not exist."""

    _parent_create_stop_path: Path
    """The resolved path of a directory that is at or above the directory managed by this object, and
       which will never be created by this object. If this path is the same as _dir_path, then the directory
       managed by this path will not be created. If _dir_path.anchor, then all parent directories up to the root will
       be created if needed. If this path does not exist, any attempt to create files or subdirectories within the private
       directory will fail."""
       
    _parent_fix_permissions_stop_path: Path
    """The resolved path of a directory that is at or above the directory managed by this object. No existing directory
       at or above this path will have its permissions modified by this object as part of the
       implicit directory creation logic. If equal to _dir_path, then no existing directories
       will have their permissions modified. If _dir_path.anchor, then all existing parent directories up to the root will
       have their permissions modified to 0700 if needed.
       Does not affect creation and setting of permissions for new directories."""
       
    _parent_verify_permissions_stop_path: Path
    """The resolved path of a directory that is at or above the directory managed by this object. No existing directory
         at or above this path will have its permissions verified by this object as part of the
         implicit directory verification logic. If equal to _dir_path, then no existing directories
         will have their permissions verified. If _dir_path.anchor, then all existing parent directories up to the root will
         have their permissions verified to be 0700 if needed.
         Does not affect creation and setting of permissions for new directories."""
       
    _dir_created: bool = False
    """Whether the private directory has been created and had it's permissions fixed according to policy."""
    
    def __init__(
                self,
                dir_path: Path | str,
                *,
                parent_create_stop_path: Path | str | None = None,
                parent_fix_permissions_stop_path: Path | str | None = None,
                parent_verify_permissions_stop_path: Path | str | None = None
            ):
        """Creates a private directory manager for a specified directory, which may or may not exist yet.

        Args:
            dir_path (Path | str):
                The path of the directory. If relative, it is relative to the current working directory.
            parent_create_stop_path (Path | str | None):
                The path at which to never create a parent directory. If relative, it is relative to dir_path, and may include "..".
                Must resolve to dir_path or some parent directory of it. If equal to dir_path, then dir_path will not
                be created. If dir_path.anchor, then all parent directories up to dir_path will be created if needed.
                If this path does not exist, any attempt to create files or subdirectories by this object will fail.
                If None (the default), ".." is used, which means that dir_path can be created, but none of its parents.
            parent_fix_permissions_stop_path (Path | str | None):
                The path at which to never modify permissions on an existing directory. If relative, it is relative to dir_path,
                and may include "..". Must resolve to dir_path or some parent directory of it. If equal to dir_path,
                then permissions on an existing dir_path will not be modified.
                If dir_path.anchor, then all existing parent directories up to dir_path will have permissions modified.
                If None (the default), ".." is used, which means that dir_path can have permissions modified, but none of its parents.
            parent_verify_permissions_stop_path (Path | str | None):
                The path at which to never verify permissions on an existing directory. If relative, it is relative to dir_path,
                and may include "..". Must resolve to dir_path or some parent directory of it. If equal to dir_path,
                then permissions on an existing dir_path will not be verified.
                If dir_path.anchor, then all existing parent directories up to dir_path will have permissions verified.
                If None (the default), parent_fix_permissions_stop_path is used.
        """
        dir_path = Path(dir_path).resolve()
        parent_create_stop_path = (
                dir_path / (Path(parent_create_stop_path) if parent_create_stop_path is not None else Path(".."))
            ).resolve()
        parent_fix_permissions_stop_path = (
                dir_path / (Path(parent_fix_permissions_stop_path) if parent_fix_permissions_stop_path is not None else Path(".."))
            ).resolve()
        parent_verify_permissions_stop_path = (
                (dir_path / Path(parent_verify_permissions_stop_path)).resolve()
                if parent_verify_permissions_stop_path is not None
                else parent_fix_permissions_stop_path
            )
        
        self._dir_path = dir_path
        self._parent_create_stop_path = parent_create_stop_path
        self._parent_fix_permissions_stop_path = parent_fix_permissions_stop_path
        self._parent_verify_permissions_stop_path = parent_verify_permissions_stop_path
        
        
    def get_root_dir(self) -> Path:
        """Get the directory managed by this object.

        By default, this just returns the dir_path passed to the constructor, but subclasses can
        override this to compute it dynamically.

        Does not create the directory or guarantee any particular permissions, so the returned
        directory may not be safe for storing sensitive data until create_root_dir has been called.
        """
        return self._dir_path
    
    def create_root_dir(self) -> Path:
        """Create and return the private root directory, if it does not already exist.
           Limited to policy for creating directories."""
        root_dir = self.get_root_dir()
        if not self._dir_created:
            # Create directories according to policy:
            for partial_dir in _get_partial_dirs(root_dir, stop_dir=self._parent_create_stop_path):
                if not partial_dir.is_dir():
                    # This will fail by design if the pathname exists but is not a directory, or if the
                    # parent directory does not exist.
                    _mkdir_private(partial_dir)
            # Fix permissions on existing directories according to policy:
            if sys.platform != "win32":
                for partial_dir in _get_partial_dirs(root_dir, stop_dir=self._parent_fix_permissions_stop_path):
                    if partial_dir.is_dir():
                        # If it's not a directory we will fail the is_dir check below
                        _fix_dir_perms(partial_dir)
                # Verify permissions on existing directories according to policy:
                for partial_dir in _get_partial_dirs(root_dir, stop_dir=self._parent_verify_permissions_stop_path):
                    _verify_dir_perms(partial_dir)
            self._dir_created = True
        if not root_dir.exists():
            raise FileNotFoundError(f"Expected directory {str(root_dir)!r} to exist, but it does not.")
        if not root_dir.is_dir():
            raise NotADirectoryError(f"Expected {str(root_dir)!r} to be a directory, but it is not.")
                
        return root_dir
    
    def verify_root_dir(self) -> Path:
        """Verify that the private root directory (and parent directories as set in policy)
           exist and have permissions 0700. Raises an exception if not."""
        root_dir = self.get_root_dir()
        if not root_dir.exists():
            raise FileNotFoundError(f"Expected directory {str(root_dir)!r} to exist, but it does not.")
        if not root_dir.is_dir():
            raise NotADirectoryError(f"Expected {str(root_dir)!r} to be a directory, but it is not.")
        # Verify permissions on existing directories according to policy:
        if sys.platform != "win32":
            for partial_dir in _get_partial_dirs(root_dir, stop_dir=self._parent_verify_permissions_stop_path):
                _verify_dir_perms(partial_dir)
                
        return root_dir

    def get_private_dir(self, subdir: str | Path) -> Path:
        """Return the directory managed by this object, or a subdirectory thereof.

        Verifies that the resolved path is within the directory managed by this object, but does
        not create the directory or guarantee any particular permissions.
        """
        root_dir = self.get_root_dir()
        subdir_fullpath = (root_dir / subdir).resolve()
        if not subdir_fullpath.is_relative_to(root_dir):
            raise ValueError(
                    f"Expected private subdir {str(subdir)!r} to resolve to a path "
                    f"within the directory {str(root_dir)!r} managed by this object, but it does not."
                )
        return subdir_fullpath

    def create_private_dir(self, subdir: str | Path) -> Path:
        """Create and return the directory managed by this object, or a subdirectory thereof,
        with mode 0700, if it does not already exist.

        Parent directories will be created as needed, up to the root directory managed
        by this object.

        If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.

        If subdir is a relative path, it is resolved relative to the root directory managed by
        this object. Regardless, it must resolve to that root directory or a subdirectory of it.
        For example, "." can be used to refer to the root directory itself.
        """
        root_dir = self.create_root_dir()
        subdir_fullpath = self.get_private_dir(subdir)
        for partial_dir in _get_partial_dirs(subdir_fullpath, stop_dir=root_dir):
            if not partial_dir.is_dir():
                # This will fail by design if the pathname exists but is not a directory, or if the
                # parent directory does not exist.
                _mkdir_private(partial_dir)
            # Fix permissions on existing directories according to policy:
            if sys.platform != "win32":
                _fix_dir_perms(partial_dir)
            
        return subdir_fullpath
    
    def get_subdir_manager(self, subdir: str | Path, create: bool = True) -> PrivateDirManager:
        """Creates a new self-contained PrivateDirManager for a subdirectory of the directory
           managed by this object. The new manager is scoped to that subdirectory: it will never
           create, fix, or verify permissions on anything at or above it, including this object's
           own root directory.

           If create is True (the default), the root directory managed by this object and the
           subdirectory are both created (with permissions fixed/verified) if they don't already
           exist. If False, nothing is created or modified -- the subdirectory path is only
           resolved and validated.
        """
        subdir_fullpath = self.create_private_dir(subdir) if create else self.get_private_dir(subdir)
        return PrivateDirManager(
                subdir_fullpath,
                parent_create_stop_path=self._parent_create_stop_path,
                parent_fix_permissions_stop_path=self._parent_fix_permissions_stop_path,
                parent_verify_permissions_stop_path=self._parent_verify_permissions_stop_path
            )

    def delete_private_dir(self, subdir: str | Path) -> None:
        """Delete the directory managed by this object, or a subdirectory thereof, if it exists.

        If the directory does not exist, nothing happens. If the directory cannot be deleted, an
        exception will be raised.

        If subdir is a relative path, it is resolved relative to the root directory managed by
        this object. Regardless, it must resolve to that root directory or a subdirectory of it.
        For example, "." can be used to refer to the root directory itself.

        Deletion of the root directory managed by this object itself is not allowed.
        """
        subdir_fullpath = self.get_private_dir(subdir)
        if subdir_fullpath == self.get_root_dir():
            raise ValueError(
                    f"Deletion of the root directory {str(subdir_fullpath)!r} managed by this object is not allowed. "
                    "Please delete a subdirectory instead."
                )
        if not subdir_fullpath.is_dir():
            raise NotADirectoryError(f"Expected {str(subdir_fullpath)!r} to be a directory, but it is not.")
        shutil.rmtree(subdir_fullpath)
        
    def verify_private_dir(self, subdir: str | Path) -> Path:
        """Verify that the directory managed by this object, or a subdirectory thereof, exists
        and has permissions 0700.

        If the directory does not exist or does not have permissions 0700, an exception will be raised.

        If subdir is a relative path, it is resolved relative to the root directory managed by
        this object. Regardless, it must resolve to that root directory or a subdirectory of it.
        For example, "." can be used to refer to the root directory itself.
        """
        root_dir = self.verify_root_dir()
        subdir_full = self.get_private_dir(subdir)
        for partial_dir in _get_partial_dirs(subdir_full, stop_dir=root_dir):
            if not partial_dir.exists():
                raise FileNotFoundError(f"Expected directory {str(partial_dir)!r} to exist, but it does not.")
            if not partial_dir.is_dir():
                raise NotADirectoryError(f"Expected {str(partial_dir)!r} to be a directory, but it is not.")
            if sys.platform != "win32":
                _verify_dir_perms(partial_dir)
        return subdir_full

    def _resolve_private_file(self, filename: str | Path, subdir: str | Path) -> Path:
        """Resolve filename within subdir of the directory managed by this object, verifying that
           it stays within that subdirectory. Does not touch the filesystem beyond path resolution
           -- the parent directory is neither verified nor created."""
        subdir_path = self.get_private_dir(subdir)
        file_path = (subdir_path / filename).resolve()
        if not file_path.is_relative_to(subdir_path):
            raise ValueError(
                    f"Expected private file {str(filename)!r} to resolve to a path "
                    f"within the directory {str(subdir_path)!r} managed by this object, but it does not."
                )
        return file_path

    def get_private_file(
                self,
                filename: str | Path,
                create_parent: bool = False,
                subdir: str | Path = ".",
            ) -> Path:
        """Get the fully qualified path to a file with the given name within the directory
            managed by this object.

            subdir is resolved relative to the root directory managed by this object, and
            filename is resolved relative to subdir. The file must resolve to a path within the
            specified subdirectory, or a ValueError will be raised.

            If create_parent is True, the parent directory/directories, up to and including the
            root directory managed by this object, will be created if they do not already exist,
            and their permissions will be adjusted to 0700.

            If create_parent is False, the directory is not created but the existence and permissions of
            all parent directories are verified.

            The file itself is not created, and no guarantees are made about its existence or permissions."""
        file_path = self._resolve_private_file(filename, subdir)
        parent_dir = file_path.parent
        if create_parent:
            self.create_private_dir(parent_dir)
        else:
            self.verify_private_dir(parent_dir)
        return file_path

    def looks_encrypted(self, filename: str | Path, *, subdir: str | Path = ".") -> bool:
        """Return True if filename (resolved within subdir of the directory managed by this
        object, the same way open() resolves it) appears to have this library's encrypted-file
        header, without needing (or attempting to verify) a passphrase. Returns False if the file
        does not exist. Does not require the parent directory to already exist."""
        return looks_encrypted(self._resolve_private_file(filename, subdir))

    @overload
    def open(
            self, filename: str | Path, mode: OpenTextMode, *, subdir: str | Path = ".", create_parent: bool = False,
            passphrase: str | bytes | None = None, check_encryption: bool = False, atomic_update: Literal[True],
            **kwargs: Any
        ) -> AbortableTextIO: ...

    @overload
    def open(
            self, filename: str | Path, mode: OpenBinaryMode, *, subdir: str | Path = ".", create_parent: bool = False,
            passphrase: str | bytes | None = None, check_encryption: bool = False, atomic_update: Literal[True],
            **kwargs: Any
        ) -> AbortableBinaryIO: ...

    @overload
    def open(
            self, filename: str | Path, mode: OpenTextMode, *, subdir: str | Path = ".", create_parent: bool = False,
            passphrase: str | bytes | None = None, check_encryption: bool = False, atomic_update: bool = False,
            **kwargs: Any
        ) -> TextIO: ...

    @overload
    def open(
            self, filename: str | Path, mode: OpenBinaryMode, *, subdir: str | Path = ".", create_parent: bool = False,
            passphrase: str | bytes | None = None, check_encryption: bool = False, atomic_update: bool = False,
            **kwargs: Any
        ) -> BinaryIO: ...

    @overload
    def open(
            self, filename: str | Path, mode: str, *, subdir: str | Path = ".", create_parent: bool = False,
            passphrase: str | bytes | None = None, check_encryption: bool = False, atomic_update: bool = False,
            **kwargs: Any
        ) -> IO[Any]: ...
    
    def open(
                self,
                filename: str | Path,
                mode: str = "r",
                *args: Any,
                subdir: str | Path = ".",
                create_parent: bool = False,
                passphrase: str | bytes | None = None,
                check_encryption: bool = False,
                atomic_update: bool = False,
                temp_file_extension: str = ".tmp",
                **kwargs: Any
            ) -> IO[Any]:
        """Open a file with the given name within the directory managed by this object and subdirectory,
        creating the directory if necessary. If writing to the file, ensure that it has permissions 0600
        (readable and writable only by the user).

        If passphrase is given, the file is transparently encrypted at rest with a key derived from the
        passphrase (Argon2id) and AES-256-GCM authenticated encryption. Encryption/decryption is performed
        as a whole (in memory) rather than streamed, so all read/write/seek combinations, including
        update ("r+") and append ("a"/"a+") modes, work normally -- there are no seek restrictions.
        Raises DecryptionError (or a subclass) on a wrong passphrase, an unsupported/missing format
        header, or corrupted/tampered data.

        check_encryption only matters when passphrase is not given, and defaults to False so that
        reading a file's raw encrypted bytes on purpose (e.g. to back it up or copy it elsewhere)
        is never blocked. When check_encryption=True and mode reads existing content ("r"/"a"),
        its header is checked against the bytes already read into memory; if it looks like it was
        written with a passphrase, PassphraseRequiredError is raised instead of silently returning
        the raw ciphertext.

        If atomic_update is True and the file is opened for writing, a temporary file with
        extension `temp_file_extension` will be written first and then renamed to the target file
        at close time, so that the target file is never left in a partially-written state. This
        is fully atomic only on platforms where os.rename() is atomic and overwrites the target (Linux, MacOS).
        On other platforms (Windows), there will be a brief window where the target file is deleted before the
        temporary file is renamed to it.

        The returned file object also has an abort() method: calling it marks the pending write
        to be discarded rather than committed on close, whether that close happens explicitly,
        because a `with` block exits due to an exception, or because the file is
        garbage-collected without ever having been closed. This lets a caller building up content
        incrementally bail out cleanly (e.g. on a validation failure) without partially writing to
        (non-atomic_update) or replacing (atomic_update) the target file.

        Note that the use of passphrase or atomic_update will result in the complete contents
        of the file being read into memory, so this is not suitable for very large files.
        """
        allows_create = any(m in mode for m in "wax")
        file_path = self.get_private_file(filename, create_parent=allows_create or create_parent, subdir=subdir)
        use_wrapper = passphrase is not None or atomic_update
        if use_wrapper:
            return open_wrapped(
                    file_path,
                    mode,
                    self._open_standard,
                    *args,
                    passphrase=passphrase,
                    check_encryption=check_encryption,
                    atomic_update = atomic_update,
                    temp_file_extension=temp_file_extension,
                    **kwargs
                )
        if check_encryption and ("r" in mode or "a" in mode) and looks_encrypted(file_path):
            raise PassphraseRequiredError(
                f"{str(file_path)!r} appears to be passphrase-encrypted; pass passphrase= to open() to read it."
            )
        return self._open_standard(file_path, mode, **kwargs)

    @overload
    def _open_standard(
            self, file: str | Path, mode: OpenTextMode, *args: Any, **kwargs: Any
        ) -> TextIO: ...

    @overload
    def _open_standard(
            self, file: str | Path, mode: OpenBinaryMode, *args: Any, **kwargs: Any
        ) -> BinaryIO: ...

    @overload
    def _open_standard(
            self, file: str | Path, mode: str, *args: Any, **kwargs: Any
        ) -> IO[Any]: ...

    def _open_standard(self, file: str | Path, mode: str, *args: Any, **kwargs: Any) -> IO[Any]:
        """Open file_path with the real, unencrypted open(), ensuring that a file opened for
        writing ends up with permissions 0600 (readable and writable only by the user)."""
        is_write_mode = any(m in mode for m in "wax+")
        f = open(file, mode, *args, **kwargs)  # noqa: SIM115
        try:
            if is_write_mode and sys.platform != "win32":
                # On Linux and MacOS, ensure the file has mode 400 (readable only by the user).
                fd = f.fileno()
                st = os.fstat(fd)
                mode_bits = st.st_mode & 0o777
                if mode_bits != 0o600:
                    os.fchmod(fd, 0o600)
        except Exception:
            f.close()
            raise
        return f
    
class PrivateFilesManager(PrivateDirManager):
    """A class for managing private files and directories for an application."""
    
    app_name: str | None
    """The application name used to create the application-specific private directory.
       If None, a shared private parent directory not specific to an application is used.."""
       
    _shared_root_dir: Path
    """The cached shared private directory root path."""
    
    def __init__(self, app_name: str | None = None):
        self.app_name = app_name
        self._shared_root_dir = get_shared_private_dir()
        
        if sys.platform == "win32":
            # On Windows, we will not create or modify the shared private directory, because it is managed by the OS.
            parent_create_stop_path = self._shared_root_dir
        else:
            # On Linux and MacOS, we will create the shared private directory ("~/.private"),
            # because it is managed by this library. However we will not modify existing perms
            parent_create_stop_path = self._shared_root_dir.parent
            
        # On all platforms, we will not modify existing perms on the shared private directory,
        # because it is managed by the OS or the user. However, we will verify that it has the correct
        # permissions on Linux and MacOS.
        parent_fix_permissions_stop_path = self._shared_root_dir
        parent_verify_permissions_stop_path = self._shared_root_dir.parent
        
        dir_path = (self._shared_root_dir / (app_name if app_name is not None else ".")).resolve()
        if not dir_path.is_relative_to(self._shared_root_dir):
            raise ValueError(
                    f"Expected application-specific private directory {str(app_name)!r} to resolve to a path "
                    f"within the shared private directory {str(self._shared_root_dir)!r}, but it does not."
                )
        super().__init__(
            dir_path=dir_path,
            parent_create_stop_path=parent_create_stop_path,
            parent_fix_permissions_stop_path=parent_fix_permissions_stop_path,
            parent_verify_permissions_stop_path=parent_verify_permissions_stop_path
        )
    
    def get_shared_root_dir(self) -> Path:
        """Get the name of the shared user-wide private root directory for storing sensitive data like authentication tokens.
        On linux and macos, this will be ~/.private, which the user can choose to encrypt or protect as needed.
        On Windows, this will be the non-roaming app data directory.

        Does not create the directory or guarantee any particular permissions, so the returned directory
        may not be safe for storing sensitive data until create_root_dir has been called.
        """
        return self._shared_root_dir
        

@cache
def _get_private_files_manager(app_name: str | None) -> PrivateFilesManager: # hide the @cache so that it does not screw up type
                                                                             # hinting for the public function.
    return PrivateFilesManager(app_name=app_name)

def get_private_files(app_name: str | None = None) -> PrivateFilesManager:
    """Get a cached PrivateFilesManager instance for the given application name."""
    return _get_private_files_manager(app_name)

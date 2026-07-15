"""
Support for management of semnsitiive user-wide application files such as authentication tokens and profile data.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import cache
from pathlib import Path
from typing import IO, Any, BinaryIO, Final, Literal, TextIO, TypeAlias, overload

from platformdirs import user_data_dir

__all__ =  [
    "OpenTextMode",
    "OpenBinaryMode",
    "get_shared_private_dir",
    "create_shared_private_dir",
    "PrivateFilesManager",
    "get_private_files_manager",
    "get_private_app_dir",
    "create_private_app_dir",
    "get_private_dir",
    "create_private_dir",
    "delete_private_dir",
    "verify_private_dir",
    "get_private_app_file",
    "open_private_app_file",
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
    may not be safe for storing sensitive data until create_private_dir has been called.
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
    """Create and return the shared user-wide private root directory for storing sensitive data like authentication tokens,
    if it does not already exist. On linux and macos, this will be ~/.private, which the user can choose to encrypt or protect as needed.
    On Windows, this will be the non-roaming app data directory.
    
    If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.
    """
    return _create_shared_private_dir()

class PrivateFilesManager:
    """A class for managing private files and directories for an application."""
    
    app_name: str | None
    """The application name used to create the application-specific private directory.
       If None, a shared private parent directory not specific to an application is used.."""
       
    _shared_root_dir: Path | None = None
    """The cached shared private directory root path, if it has been computed."""
    
    _shared_root_dir_created: bool = False
    """Whether the shared private directory root path has been created."""
       
    _root_dir: Path | None = None
    """The cached application-specific private directory root path, if it has been computed."""
    
    _root_dir_created: bool = False
    """Whether the application-specific private directory root path has been created."""

    def __init__(self, app_name: str | None = None):
        self.app_name = app_name
    
    def get_shared_root_dir(self) -> Path:
        """Get the name of the shared user-wide private root directory for storing sensitive data like authentication tokens.
        On linux and macos, this will be ~/.private, which the user can choose to encrypt or protect as needed.
        On Windows, this will be the non-roaming app data directory.
        
        Does not create the directory or guarantee any particular permissions, so the returned directory
        may not be safe for storing sensitive data until create_private_dir has been called.
        """
        if self._shared_root_dir is None:
            self._shared_root_dir = get_shared_private_dir()
        return self._shared_root_dir
    
    def create_shared_root_dir(self) -> Path:
        """Create and return the shared user-wide private root directory for storing sensitive data
        like authentication tokens, if it does not already exist. On linux and macos, this will be
        ~/.private, which the user can choose to encrypt or protect as needed.
        On Windows, this will be the non-roaming app data directory.
        
        If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.
        """
        shared_root_dir = self.get_shared_root_dir()
        if not self._shared_root_dir_created:
            create_shared_private_dir()
            self._shared_root_dir_created = True
        return shared_root_dir
        
    def get_root_dir(self) -> Path:
        """Get the name of the application-specific user-wide private root directory for storing sensitive data like authentication tokens.
        On linux and macos, this will be a directory under ~/.private, which the user can choose to encrypt or protect as needed.
        On Windows, this will be the non-roaming app data directory.
        
        Does not create the directory or guarantee any particular permissions, so the returned directory
        may not be safe for storing sensitive data until create_private_dir has been called.
        """
        app_name = self.app_name
        if self._root_dir is None:
            shared_root_dir = self.get_shared_root_dir()
            root_dir = (shared_root_dir / (app_name if app_name else ".")).resolve()
            if not root_dir.is_relative_to(shared_root_dir):
                raise ValueError(
                        f"Expected application-specific private directory {str(app_name)!r} to resolve to a path "
                        f"within the shared private directory {str(self.get_shared_root_dir())!r}, but it does not."
                    )
            self._root_dir = root_dir
        return self._root_dir
    
    def create_root_dir(self) -> Path:
        """Create and return the application-specific user-wide private root directory for storing sensitive data
        like authentication tokens, if it does not already exist. On Linux and MacOS, this will be a directory
        under ~/.private with mode 0700. On Windows, this will be the non-roaming app data directory.
        
        If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.
        """
        root_dir = self.get_root_dir()
        if not self._root_dir_created:
            shared_root_dir = self.create_shared_root_dir()
            if not root_dir.is_relative_to(shared_root_dir):
                raise ValueError(
                        f"Expected application-specific private directory {str(root_dir)!r} to resolve to a path "
                        f"within the shared private directory {str(shared_root_dir)!r}, but it does not."
                    )
            rel_dir = root_dir.relative_to(shared_root_dir)
            partial_dir = shared_root_dir
            for component in rel_dir.parts:
                partial_dir = partial_dir / component
                old_umask = os.umask(0o077)
                try:
                    os.makedirs(partial_dir, mode=0o700, exist_ok=True)
                finally:
                    os.umask(old_umask)
                if sys.platform != "win32":
                    if not partial_dir.is_dir():
                        raise NotADirectoryError(f"Expected {str(partial_dir)!r} to be a directory, but it is not.")
                    current_mode = partial_dir.stat().st_mode & 0o777
                    if current_mode != 0o700:
                        # For our private app-specific subdir, we go ahead and fix the permissions if they are not correct,
                        partial_dir.chmod(0o700)
            self._root_dir_created = True
                
        return root_dir

    def get_private_dir(self, subdir: str | Path) -> Path:
        """Return the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
        like authentication tokens. On Linux and MacOS, this will be a directory
        under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
        
        Verifies that the resolved path is within the app-specific private directory, but does not
            create the directory or guarantee any particular permissions.
        """
        app_dir = self.get_root_dir()
        subdir_fullpath = (app_dir / subdir).resolve()
        if not subdir_fullpath.is_relative_to(app_dir):
            raise ValueError(
                    f"Expected private subdir {str(subdir)!r} to resolve to a path "
                    f"within the app-specific private directory {str(app_dir)!r}, but it does not."
                )
        return subdir_fullpath

    def create_private_dir(self, subdir: str | Path) -> Path:
        """Create and return the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
        like authentication tokens, if it does not already exist. On Linux and MacOS, this will be a directory
        under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
        
        If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.
        
        If subdir is a relative path, it is resolved relative to the the app-specific private root directory.
        Regardless, it must resolve to the app-specific root directory or a subdirectory of it.
        For example, "." can be used to refer to the app-specific private root directory itself.
        """
        app_dir_path = self.create_root_dir()
        subdir_fullpath = self.get_private_dir(subdir)
        subdir_path = subdir_fullpath.relative_to(app_dir_path)
        subdir_partial_path = app_dir_path
        for component in subdir_path.parts:
            subdir_partial_path = subdir_partial_path / component
            old_umask = os.umask(0o077)
            try:
                os.makedirs(subdir_partial_path, mode=0o700, exist_ok=True)
            finally:
                os.umask(old_umask)
            if sys.platform != "win32":
                if not subdir_partial_path.is_dir():
                    raise NotADirectoryError(f"Expected {str(subdir_partial_path)!r} to be a directory, but it is not.")
                current_mode = subdir_partial_path.stat().st_mode & 0o777
                if current_mode != 0o700:
                    subdir_partial_path.chmod(0o700)
        return subdir_fullpath

    def delete_private_dir(self, subdir: str | Path) -> None:
        """Delete the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
        like authentication tokens, if it exists. On Linux and MacOS, this will be a directory
        under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
        
        If the directory does not exist, nothing happens. If the directory cannot be deleted, an exception will be raised.
        
        If subdir_name is a relative path, it is resolved relative to the the app-specific private root directory.
        Regardless, it must resolve to the app-specific root directory or a subdirectory of it.
        For example, "." can be used to refer to the app-specific private root directory itself.
        
        Deletion of the shared root directory itself is not allowed.
        """
        subdir_fullpath = self.get_private_dir(subdir)
        if subdir_fullpath == self.get_shared_root_dir():
            raise ValueError(
                    f"Deletion of the shared private root directory {str(subdir_fullpath)!r} is not allowed. "
                    "Please delete application-specific ubdirectories instead."
                )
        if not subdir_fullpath.is_dir():
            raise NotADirectoryError(f"Expected {str(subdir_fullpath)!r} to be a directory, but it is not.")
        shutil.rmtree(subdir_fullpath)
        
    def verify_private_dir(self, subdir: str | Path) -> Path:
        """Verify that the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
        like authentication tokens exists and has permissions 0700. On Linux and MacOS, this will be a directory
        under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
        
        If the directory does not exist or does not have permissions 0700, an exception will be raised.
        
        If subdir_name is a relative path, it is resolved relative to the the app-specific private root directory.
        Regardless, it must resolve to the app-specific root directory or a subdirectory of it.
        For example, "." can be used to refer to the app-specific private root directory itself.
        """
        app_dir = self.get_root_dir()
        app_dir_parent = app_dir.parent
        subdir_full = self.get_private_dir(subdir)
        subdir_relpath = subdir_full.relative_to(app_dir_parent)
        subdir_partial_path = app_dir_parent
        for component in subdir_relpath.parts:
            subdir_partial_path = subdir_partial_path / component
            if not subdir_partial_path.is_dir():
                raise NotADirectoryError(f"Expected {str(subdir_partial_path)!r} to be a directory, but it is not.")
            if sys.platform != "win32":
                current_mode = subdir_partial_path.stat().st_mode & 0o777
                if current_mode != 0o700:
                    raise PermissionError(
                            f"Expected {str(subdir_partial_path)!r} to have permissions 0700, but it has permissions {current_mode:04o}. "
                            "Please set the permissions to 0700 to protect your sensitive data."
                        )
        return subdir_full
    
    def get_private_file(
                self,
                filename: str | Path,
                create_parent: bool = False,
                subdir: str | Path = ".",
            ) -> Path:
        """Get the fully qualified path to a file with the given name in the application-specific
        user-wide private directory for storing sensitive data like authentication tokens.
        
        subdir_name is resolved relative to the app-specific private root directory,
        and filename is resolved relative to subdir_name. The file must resolve
        to a file within the specified subdirectory, or a ValueError will be raised.
        
        If create_parent is True, the parent directory/directories, up to and including the application-specific root
        directory, will be created if they do not already exist, and their permissions will be adjusted to 0700.
        
        If create_parent is False, the directory is not created but the existence and permissions of
        all parent directories are verified.
        
        The file itself is not created, and no guarantees are made about its existence or permissions."""
        subdir_path = self.get_private_dir(subdir)
        file_path = (subdir_path / filename).resolve()
        if not file_path.is_relative_to(subdir_path):
            raise ValueError(
                    f"Expected private file {str(filename)!r} to resolve to a path "
                    f"within the app-specific private directory {str(subdir_path)!r}, but it does not."
                )
        parent_dir = file_path.parent
        if create_parent:
            self.create_private_dir(parent_dir)
        else:
            self.verify_private_dir(parent_dir)
        return file_path

    @overload
    def open(
            self, filename: str | Path, mode: OpenTextMode, *, subdir: str | Path = ".", create_parent: bool = False, **kwargs: Any
        ) -> TextIO: ...

    @overload
    def open(
            self, filename: str | Path, mode: OpenBinaryMode, *, subdir: str | Path = ".", create_parent: bool = False, **kwargs: Any
        ) -> BinaryIO: ...

    @overload
    def open(
            self, filename: str | Path, mode: str, *, subdir: str | Path = ".", create_parent: bool = False, **kwargs: Any
        ) -> IO[Any]: ...

    def open(
                self,
                filename: str | Path,
                mode: str = "r", *,
                subdir: str | Path = ".",
                create_parent: bool = False,
                **kwargs: Any
            ) -> IO[Any]:
        """Open a file with the given name in the application-specific user-wide private directory and subdirectory,
        creating the directory if necessary. If writing to the file, ensure that it has permissions 0600
        (readable and writable only by the user)."""
        
        allows_create = any(m in mode for m in "wax")
        is_write_mode = any(m in mode for m in "wax+")
        file_path = self.get_private_file(filename, create_parent=allows_create or create_parent, subdir=subdir)
        f = open(file_path, mode, **kwargs)  # noqa: SIM115
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

@cache
def _get_private_files_manager(app_name: str | None) -> PrivateFilesManager: # hide the @cache so that it does not screw up type
                                                                             # hinting for the public function.
    return PrivateFilesManager(app_name=app_name)

def get_private_files_manager(app_name: str | None = None) -> PrivateFilesManager:
    """Get a cached PrivateFilesManager instance for the given application name."""
    
    return _get_private_files_manager(app_name)

def get_private_app_dir(app_name: str | None = None) -> Path:
    """Return the application-specific user-wide private directory for storing sensitive data
       like authentication tokens. On Linux and MacOS, this will be a directory
       under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
    """
    return get_private_files_manager(app_name).get_root_dir()

def create_private_app_dir(app_name: str | None = None) -> Path:
    """Create and return the application-specific user-wide private root directory for storing sensitive data
    like authentication tokens, if it does not already exist. On Linux and MacOS, this will be a directory
    under ~/.private with mode 0700. On Windows, this will be the non-roaming app data directory.
    
    If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.
    """
    return get_private_files_manager(app_name).create_root_dir()

def get_private_dir(subdir: str | Path, app_name: str | None = None) -> Path:
    """Return the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
       like authentication tokens. On Linux and MacOS, this will be a directory
       under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
       
       Verifies that the resolved path is within the app-specific private directory, but does not
         create the directory or guarantee any particular permissions.
    """
    return get_private_files_manager(app_name).get_private_dir(subdir)

def create_private_dir(subdir: str | Path, app_name: str | None = None) -> Path:
    """Create and return the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
       like authentication tokens, if it does not already exist. On Linux and MacOS, this will be a directory
       under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
       
       If the directory cannot be created or cannot be set to the correct permissions, an exception will be raised.
       
       If subdir_name is a relative path, it is resolved relative to the the app-specific private root directory.
       Regardless, it must resolve to the app-specific root directory or a subdirectory of it.
       For example, "." can be used to refer to the app-specific private root directory itself.
    """
    return get_private_files_manager(app_name).create_private_dir(subdir)

def delete_private_dir(subdir_name: str | Path, app_name: str) -> None:
    """Delete the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
       like authentication tokens, if it exists. On Linux and MacOS, this will be a directory
       under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
       
       If the directory does not exist, nothing happens. If the directory cannot be deleted, an exception will be raised.
       
       If subdir_name is a relative path, it is resolved relative to the the app-specific private root directory.
       Regardless, it must resolve to the app-specific root directory or a subdirectory of it.
       For example, "." can be used to refer to the app-specific private root directory itself.
       
       Deletion of the shared root directory itself is not allowed.
    """
    subdir_fullpath = get_private_dir(subdir_name, app_name=app_name)
    if subdir_fullpath.is_dir():
        shutil.rmtree(subdir_fullpath)

def verify_private_dir(subdir_name: str | Path, app_name: str | None = None) -> Path:
    """Verify that the application-specific user-wide private directory or a subdirectory thereof for storing sensitive data
       like authentication tokens exists and has permissions 0700. On Linux and MacOS, this will be a directory
       under ~/.private/{app_name} with mode 0700. On Windows, this will be under the non-roaming app data directory.
       
       If the directory does not exist or does not have permissions 0700, an exception will be raised.
       
       If subdir_name is a relative path, it is resolved relative to the the app-specific private root directory.
       Regardless, it must resolve to the app-specific root directory or a subdirectory of it.
       For example, "." can be used to refer to the app-specific private root directory itself.
    """
    return get_private_files_manager(app_name).verify_private_dir(subdir_name)

def get_private_app_file(
            filename: str | Path,
            *,
            create_parent: bool = False,
            subdir: str | Path = ".",
            app_name: str | None = None,
        ) -> Path:
    """Get the fully qualified path to a file with the given name in the application-specific
       user-wide private directory for storing sensitive data like authentication tokens.
       
       subdir_name is resolved relative to the app-specific private root directory,
       and filename is resolved relative to subdir_name. The file must resolve to a file within the
       specified subdirectory, or a ValueError will be raised.
       
       If create_parent is True, the parent directory/directories, up to and including the application-specific root
       directory, will be created if they do not already exist, and their permissions will be adjusted to 0700.
       
       If create_parent is False, the directory is not created but the existence and permissions of
       all parent directories are verified.
       
       The file itself is not created, and no guarantees are made about its existence or permissions."""
    return get_private_files_manager(app_name).get_private_file(
            filename=filename,
            create_parent=create_parent,
            subdir=subdir,
        )

@overload
def open_private_app_file(
        filename: str | Path, mode: OpenTextMode, *, subdir: str | Path = ".",
        create_parent: bool = False, app_name: str | None = None, **kwargs: Any
    ) -> TextIO: ...

@overload
def open_private_app_file(
        filename: str | Path, mode: OpenBinaryMode, *, subdir: str | Path = ".",
        create_parent: bool = False, app_name: str | None = None, **kwargs: Any
    ) -> BinaryIO: ...

@overload
def open_private_app_file(
        filename: str | Path, mode: str, *, subdir: str | Path = ".",
        create_parent: bool = False, app_name: str | None = None, **kwargs: Any
    ) -> IO[Any]: ...

def open_private_app_file(
            filename: str | Path,
            mode: str = "r",
            *,
            subdir: str | Path = ".",
            create_parent: bool = False,
            app_name: str | None = None,
            **kwargs: Any
        ) -> IO[Any]:
    """Open a file with the given name in the application-specific user-wide private directory and subdirectory,
       creating the directory if necessary. This is a convenience wrapper around get_private_app_file and open."""
    return get_private_files_manager(app_name).open(
            filename=filename,
            mode=mode,
            subdir=subdir,
            create_parent=create_parent,
            **kwargs
        )

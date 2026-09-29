"""Private local quarantine storage for Linux deployments.

No client filenames are used on disk. This module handles bytes only; callers
must authorize requests, reserve aggregate quota, validate content, and manage
database references. Do not expose this directory through the web server.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat

from .requirements import MAX_IMAGE_BYTES


CHUNK_BYTES = 1024 * 1024
_KEY = re.compile(r"[0-9a-f]{32}\.blob", re.ASCII)


class StorageError(Exception):
    """Storage configuration or object safety check failed."""


class UploadTooLarge(StorageError):
    """The stream exceeds its per-file or reserved remaining byte budget."""


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    size_bytes: int
    sha256: str


def _budget(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


class QuarantineStorage:
    """A private directory held open by descriptor; use as a context manager.

    root must be an absolute server-configured path. Parent directories must
    exist and no path component may be a symlink. Only the final directory is
    created (0700). Existing roots must be owned by this process's user and
    have no group/other access. This implementation requires POSIX dir_fd,
    O_NOFOLLOW and directory fsync; it is intentionally not a Windows fallback.
    """

    def __init__(self, root):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise StorageError("Quarantine storage requires a POSIX host with O_NOFOLLOW")
        path = Path(root)
        if not path.is_absolute() or ".." in path.parts or path == Path("/"):
            raise StorageError("Use an absolute, non-root quarantine path without '..'")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open("/", flags)
        try:
            for i, component in enumerate(path.parts[1:]):
                if i == len(path.parts) - 2:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise StorageError("Quarantine directory must be owned by the service user with mode 0700")
            self._dir_fd = fd
        except BaseException:
            os.close(fd)
            raise

    def __enter__(self):
        self._directory()
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._dir_fd is not None:
            os.close(self._dir_fd)
            self._dir_fd = None

    def _directory(self):
        if self._dir_fd is None:
            raise StorageError("Quarantine storage is closed")
        return self._dir_fd

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            raise StorageError("Invalid storage key")
        return key

    def save(self, stream, *, remaining_bytes, max_bytes=MAX_IMAGE_BYTES):
        """Stream an upload under the caller's previously reserved quota.

        max_bytes may tighten but never relax the server's per-image ceiling.
        Reads at most one byte beyond the allowed budget to detect overflow.
        Caller retains ownership of stream. Empty files are stored; validation
        later rejects them where required. Database publication happens later.
        """
        limit = min(MAX_IMAGE_BYTES, _budget(max_bytes, "max_bytes"),
                    _budget(remaining_bytes, "remaining_bytes"))
        directory = self._directory()
        temporary = ".part-" + secrets.token_hex(16)
        key = secrets.token_hex(16) + ".blob"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        linked = False
        size = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as destination:
                while True:
                    amount = min(CHUNK_BYTES, limit - size + 1)
                    chunk = stream.read(amount)
                    if not isinstance(chunk, bytes):
                        raise StorageError("Upload stream must return bytes")
                    if len(chunk) > amount:
                        raise StorageError("Upload stream exceeded the requested read size")
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise UploadTooLarge("Upload exceeds its allowed byte budget")
                    destination.write(chunk)
                    digest.update(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            # Hard-link publication is atomic and refuses to replace a prior
            # object. Both names are inside the same held-open private directory.
            os.link(temporary, key, src_dir_fd=directory, dst_dir_fd=directory,
                    follow_symlinks=False)
            linked = True
            os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        except BaseException:
            # Never remove a colliding pre-existing final object.
            if linked:
                os.unlink(key, dir_fd=directory)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            raise
        return StoredFile(key, size, digest.hexdigest())

    @contextmanager
    def open(self, storage_key):
        """Read a stored object after the caller checks ownership/permissions."""
        key = self._key(storage_key)
        fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._directory())
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise StorageError("Unsafe quarantine object")
            handle = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            yield handle

    def copy(self, storage_key, *, remaining_bytes, max_bytes=MAX_IMAGE_BYTES):
        """Give an unchanged file a distinct storage object for a new revision."""
        with self.open(storage_key) as source:
            return self.save(source, remaining_bytes=remaining_bytes, max_bytes=max_bytes)

    def delete(self, storage_key):
        """Explicit cleanup only after authorization/reference checks by caller.

        Returns False if already absent. Unlinks this directory entry without
        following symlinks. Does not delete records or traverse directories.
        """
        key = self._key(storage_key)
        directory = self._directory()
        try:
            os.unlink(key, dir_fd=directory)
        except FileNotFoundError:
            return False
        os.fsync(directory)
        return True

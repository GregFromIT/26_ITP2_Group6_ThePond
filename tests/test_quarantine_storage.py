"""Filesystem safety and streaming checks, confined to pytest temp folders."""

import hashlib
import io
import os
import stat

import pytest

from challenge_ingestion import storage as module
from challenge_ingestion.storage import QuarantineStorage, StorageError, UploadTooLarge


@pytest.fixture
def store(tmp_path):
    with QuarantineStorage(tmp_path / "quarantine") as storage:
        yield storage


def test_store_hash_size_permissions_and_read(store, tmp_path):
    data = b"image-placeholder" * 100000
    source = io.BytesIO(data)
    result = store.save(source, remaining_bytes=len(data))
    assert not source.closed
    assert result.size_bytes == len(data)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    with store.open(result.storage_key) as handle:
        assert handle.read() == data
    root = tmp_path / "quarantine"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / result.storage_key).stat().st_mode) == 0o600
    assert [p.name for p in root.iterdir()] == [result.storage_key]


@pytest.mark.parametrize("max_bytes,remaining", [(3, 100), (100, 3), (0, 100)])
def test_limits_remove_partial_files(store, tmp_path, max_bytes, remaining):
    with pytest.raises(UploadTooLarge):
        store.save(io.BytesIO(b"1234"), max_bytes=max_bytes, remaining_bytes=remaining)
    assert list((tmp_path / "quarantine").iterdir()) == []


def test_policy_limit_cannot_be_relaxed_by_argument(store, monkeypatch):
    monkeypatch.setattr(module, "MAX_IMAGE_BYTES", 3)
    with pytest.raises(UploadTooLarge):
        store.save(io.BytesIO(b"1234"), max_bytes=100, remaining_bytes=100)


def test_zero_byte_file_and_exact_limit_allowed(store):
    empty = store.save(io.BytesIO(b""), remaining_bytes=0)
    exact = store.save(io.BytesIO(b"abc"), max_bytes=3, remaining_bytes=3)
    assert empty.size_bytes == 0 and exact.size_bytes == 3


@pytest.mark.parametrize("budget", [-1, True, 1.5, None, "100"])
def test_invalid_budgets_rejected(store, budget):
    with pytest.raises(ValueError):
        store.save(io.BytesIO(b""), remaining_bytes=budget)


def test_interrupted_upload_cleans_up(store, tmp_path):
    class BrokenStream:
        count = 0
        def read(self, amount):
            self.count += 1
            if self.count == 1:
                return b"abc"
            raise OSError("Client disconnected")
    with pytest.raises(OSError, match="disconnected"):
        store.save(BrokenStream(), remaining_bytes=100)
    assert list((tmp_path / "quarantine").iterdir()) == []


def test_nonbinary_stream_rejected(store, tmp_path):
    with pytest.raises(StorageError, match="bytes"):
        store.save(io.StringIO("text"), remaining_bytes=100)
    assert list((tmp_path / "quarantine").iterdir()) == []


def test_overflow_reads_only_one_extra_byte(store):
    source = io.BytesIO(b"a" * 100)
    with pytest.raises(UploadTooLarge):
        store.save(source, remaining_bytes=3)
    assert source.tell() == 4


@pytest.mark.parametrize("key", ["../secret", "/etc/passwd", "sub/file", "a" * 32 + ".blob\n", "A" * 32 + ".blob", None])
def test_invalid_keys_rejected_for_read_and_delete(store, key):
    with pytest.raises(StorageError):
        with store.open(key):
            pass
    with pytest.raises(StorageError):
        store.delete(key)


def test_root_and_ancestor_symlinks_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    for path in [alias, alias / "quarantine"]:
        with pytest.raises(OSError):
            QuarantineStorage(path)
    assert not (real / "quarantine").exists()


def test_broad_permissions_rejected_without_chmod(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    root.chmod(0o755)
    with pytest.raises(StorageError, match="0700"):
        QuarantineStorage(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


def test_symlink_object_not_read_or_followed_on_delete(store, tmp_path):
    target = tmp_path / "outside"
    target.write_bytes(b"preserve")
    key = "a" * 32 + ".blob"
    (tmp_path / "quarantine" / key).symlink_to(target)
    with pytest.raises(OSError):
        with store.open(key):
            pass
    assert store.delete(key)
    assert target.read_bytes() == b"preserve"


@pytest.mark.parametrize("kind", ["fifo", "directory", "hardlink"])
def test_nonregular_and_shared_objects_rejected(store, tmp_path, kind):
    path = tmp_path / "quarantine" / ("b" * 32 + ".blob")
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    else:
        outside = tmp_path / "outside"
        outside.write_bytes(b"data")
        outside.chmod(0o600)
        os.link(outside, path)
    with pytest.raises(StorageError):
        with store.open(path.name):
            pass


def test_collision_does_not_overwrite_or_delete_existing(store, tmp_path, monkeypatch):
    existing = store.save(io.BytesIO(b"keep"), remaining_bytes=100)
    tokens = iter(["c" * 32, existing.storage_key[:-5]])
    monkeypatch.setattr(module.secrets, "token_hex", lambda size: next(tokens))
    with pytest.raises(FileExistsError):
        store.save(io.BytesIO(b"replace"), remaining_bytes=100)
    with store.open(existing.storage_key) as handle:
        assert handle.read() == b"keep"
    assert len(list((tmp_path / "quarantine").iterdir())) == 1


def test_revision_copy_is_independent_and_cleanup_is_idempotent(store):
    first = store.save(io.BytesIO(b"same"), remaining_bytes=100)
    second = store.copy(first.storage_key, remaining_bytes=100)
    assert first.storage_key != second.storage_key
    assert first.sha256 == second.sha256 and first.size_bytes == second.size_bytes
    assert store.delete(first.storage_key)
    assert not store.delete(first.storage_key)
    with store.open(second.storage_key) as handle:
        assert handle.read() == b"same"


def test_directory_swap_does_not_redirect_existing_store(store, tmp_path):
    original = tmp_path / "quarantine"
    moved = tmp_path / "moved"
    original.rename(moved)
    original.mkdir(mode=0o700)
    result = store.save(io.BytesIO(b"data"), remaining_bytes=100)
    assert (moved / result.storage_key).is_file()
    assert list(original.iterdir()) == []


def test_write_failure_cleans_partial_object(store, tmp_path, monkeypatch):
    def fail(fd):
        raise OSError("Disk failure")
    monkeypatch.setattr(module.os, "fsync", fail)
    with pytest.raises(OSError, match="Disk failure"):
        store.save(io.BytesIO(b"data"), remaining_bytes=100)
    assert list((tmp_path / "quarantine").iterdir()) == []


def test_closed_store_rejects_operations(store):
    store.close()
    store.close()
    with pytest.raises(StorageError, match="closed"):
        store.save(io.BytesIO(b"data"), remaining_bytes=100)


def test_directory_sync_failure_removes_unreturned_object(store, tmp_path, monkeypatch):
    original = module.os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("Directory sync failure")
        return original(fd)
    monkeypatch.setattr(module.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="Directory sync failure"):
        store.save(io.BytesIO(b"data"), remaining_bytes=100)
    assert list((tmp_path / "quarantine").iterdir()) == []


def test_stream_ignoring_read_limit_rejected(store, tmp_path):
    class UnboundedStream:
        def read(self, amount):
            return b"x" * (amount + 1)
    with pytest.raises(StorageError, match="read size"):
        store.save(UnboundedStream(), remaining_bytes=3)
    assert list((tmp_path / "quarantine").iterdir()) == []

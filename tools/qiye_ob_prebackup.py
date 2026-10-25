#!/usr/bin/env python3
"""Fail-closed, offline/online prebackup for the *Zeabur 栖野* OB volume.

No source writes, service stops, provider calls, MCP calls, or uploads.
This is NOT a final cutover snapshot: a separately approved write freeze,
final snapshot, offsite retrieval verification and single-writer cutover remain.
The output archive contains PRIVATE buckets, config and potentially secrets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import stat
import tarfile
import tempfile
from pathlib import Path

SERVICE_PREFIX = "service-6a51f4daf04125ac9a34c5da"
VERSION = "3.6.13"
DB_NAMES = frozenset({"embeddings.db", "dehydration_cache.db"})
MAX_FILES = 20000
MAX_BYTES = 1024 * 1024 * 1024
BLOCK = 1024 * 1024


class Refusal(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    # O_NOFOLLOW prevents a symlink swap between lstat and open.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as source:
        for chunk in iter(lambda: source.read(BLOCK), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect(root: Path) -> dict[str, tuple[int, int, str, int]]:
    """Record a full content-hash inventory; refuse symlinks and special files."""
    found = {}
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for name in dirs:
            p = Path(current) / name
            if not stat.S_ISDIR(p.lstat().st_mode):
                raise Refusal("unsafe_directory_type")
        for name in files:
            p = Path(current) / name
            meta = p.lstat()
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                raise Refusal("unsafe_file_type_or_hardlink")
            rel = p.relative_to(root).as_posix()
            if rel in found or name.endswith(("-wal", "-shm", "-journal")):
                raise Refusal("sidecar_or_duplicate")
            if name.endswith(".db") and rel not in DB_NAMES:
                raise Refusal("unapproved_database")
            with os.fdopen(os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as src:
                header = src.read(16)
            if header == b"SQLite format 3\x00" and rel not in DB_NAMES:
                raise Refusal("unapproved_sqlite_header")
            total += meta.st_size
            if len(found) >= MAX_FILES or total > MAX_BYTES:
                raise Refusal("source_limit_exceeded")
            found[rel] = (meta.st_size, meta.st_mtime_ns, digest(p), stat.S_IMODE(meta.st_mode))
    if not all(name in found for name in DB_NAMES):
        raise Refusal("required_database_missing")
    return found


def inspect_directories(root: Path) -> dict[str, tuple[int, int]]:
    """Preserve empty directories, directory metadata, and reject unsafe submounts."""
    directories = {}
    for current, dirs, _ in os.walk(root, followlinks=False):
        dirs.sort()
        for name in dirs:
            p = Path(current) / name
            mode = p.lstat().st_mode
            if not stat.S_ISDIR(mode) or os.path.ismount(p):
                raise Refusal("unsafe_directory_or_submount")
            directories[p.relative_to(root).as_posix()] = (
                stat.S_IMODE(mode), p.stat().st_mtime_ns
            )
    return directories


def validate_source(root: Path) -> None:
    if not root.is_dir() or not os.path.ismount(root):
        raise Refusal("volume_mount_required")
    if not socket.gethostname().startswith(SERVICE_PREFIX + "-"):
        raise Refusal("wrong_zeabur_service")
    for rel in ("config.yaml", "_app/src/server.py", "_app/frontend/dashboard.html"):
        if not (root / rel).is_file():
            raise Refusal("restored_file_missing")
    v = root / "_app/VERSION"
    if not v.is_file() or v.read_text(encoding="utf-8").strip() != VERSION:
        raise Refusal("runtime_version_mismatch")


def open_sources(root: Path):
    handles = {}
    try:
        for name in sorted(DB_NAMES):
            src = sqlite3.connect((root / name).as_uri() + "?mode=ro", uri=True, timeout=10)
            src.execute("PRAGMA query_only=ON")
            if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                src.close()
                raise Refusal("source_sqlite_integrity_failed")
            handles[name] = (src, src.execute("PRAGMA data_version").fetchone()[0])
        return handles
    except Exception:
        for conn, _ in handles.values():
            conn.close()
        raise


def copy_one(source: Path, target: Path, expected_sha: str) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    h = hashlib.sha256()
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as inp, target.open("xb") as out:
        source_stat = os.fstat(inp.fileno())
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
            raise Refusal("unsafe_source_race")
        for chunk in iter(lambda: inp.read(BLOCK), b""):
            out.write(chunk)
            h.update(chunk)
    if h.hexdigest() != expected_sha:
        raise Refusal("source_changed_while_copying")
    os.chmod(target, stat.S_IMODE(source_stat.st_mode))
    os.utime(target, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))


def verify_archive(
    path: Path,
    expected: dict[str, str],
    expected_directories: dict[str, tuple[int, int]],
) -> None:
    with tarfile.open(path, "r:gz") as tar:
        members = tar.getmembers()
        names = [member.name for member in members]
        wanted = set(expected) | set(expected_directories)
        if set(names) != wanted or len(names) != len(set(names)):
            raise Refusal("archive_member_mismatch")
        for member in members:
            if member.name in expected_directories:
                if not member.isdir():
                    raise Refusal("archive_directory_type_mismatch")
                mode, mtime_ns = expected_directories[member.name]
                if member.mode != mode or abs(member.mtime - (mtime_ns / 1e9)) > 2:
                    raise Refusal("archive_directory_metadata_mismatch")
                continue
            if not member.isfile() or member.issym() or member.islnk():
                raise Refusal("archive_unsafe_member")
            reader = tar.extractfile(member)
            if reader is None:
                raise Refusal("archive_read_failure")
            h = hashlib.sha256()
            for chunk in iter(lambda: reader.read(BLOCK), b""):
                h.update(chunk)
            if h.hexdigest() != expected[member.name]:
                raise Refusal("archive_digest_failure")


def prebackup(root: Path, target: Path) -> dict[str, object]:
    """Capture one stable filesystem interval and transactionally backed-up DBs.

    This DOES NOT claim that an active write was frozen or that prebackup can be
    used as the final authoritative cutover state.
    """
    root = root.resolve(strict=True)
    target = target.resolve(strict=False)
    if root == target or root in target.parents:
        raise Refusal("output_inside_source")
    if not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise Refusal("target_must_be_new")
    validate_source(root)
    baseline = inspect(root)
    original_dirs = inspect_directories(root)
    dbs = open_sources(root)
    try:
        with tempfile.TemporaryDirectory(prefix=".qiye-ob-stage-", dir=target.parent) as stage_str:
            stage = Path(stage_str)
            files_dir = stage / "files"
            files_dir.mkdir(mode=0o700)
            # Create every source directory (including empty leaves) before copying.
            for rel in sorted(original_dirs, key=lambda name: (name.count("/"), name)):
                (files_dir / rel).mkdir(parents=True, exist_ok=True)
            for rel, (_, _, sha, _) in baseline.items():
                if rel not in DB_NAMES:
                    copy_one(root / rel, files_dir / rel, sha)
            for name, (src, _) in dbs.items():
                destination = sqlite3.connect(files_dir / name)
                try:
                    src.backup(destination, pages=128, sleep=0.05)
                    if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise Refusal("backup_sqlite_integrity_failed")
                finally:
                    destination.close()
                os.chmod(files_dir / name, baseline[name][3])
                os.utime(
                    files_dir / name,
                    ns=(baseline[name][1], baseline[name][1]),
                )
            # Refuse if any non-DB file or SQLite commit changed during capture.
            validate_source(root)
            after = inspect(root)
            after_dirs = inspect_directories(root)
            if after_dirs != original_dirs:
                raise Refusal("source_directories_changed")
            for rel, entry in baseline.items():
                if rel not in DB_NAMES and after.get(rel) != entry:
                    raise Refusal("source_changed_during_backup")
            if baseline.keys() != after.keys():
                raise Refusal("source_inventory_changed")
            for src, original_version in dbs.values():
                if src.execute("PRAGMA data_version").fetchone()[0] != original_version:
                    raise Refusal("sqlite_committed_during_backup")
            hashes = {}
            for path in sorted(files_dir.rglob("*")):
                if path.is_file():
                    name = "files/" + path.relative_to(files_dir).as_posix()
                    hashes[name] = digest(path)
            if len(hashes) != len(baseline):
                raise Refusal("copied_file_count_mismatch")
            # Apply directory metadata last; copying files otherwise updates mtime.
            for rel in sorted(original_dirs, key=lambda name: (-name.count("/"), name)):
                mode, mtime_ns = original_dirs[rel]
                p = files_dir / rel
                os.chmod(p, mode)
                os.utime(p, ns=(mtime_ns, mtime_ns))
            manifest = {
                "schema": "qiye-ob-prebackup-v1",
                "service_prefix": SERVICE_PREFIX,
                "overlay_version": VERSION,
                "cutover_eligible": False,
                "file_count": len(baseline),
                "files": hashes,
                "directories": {
                    "files/" + rel: list(info) for rel, info in original_dirs.items()
                },
            }
            meta = stage / "manifest.json"
            meta.write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            hashes["manifest.json"] = digest(meta)
            expected_dirs = {
                "files/" + rel: info for rel, info in original_dirs.items()
            }
            archive = stage / "archive.tar.gz"
            with tarfile.open(archive, mode="w:gz", dereference=False) as tar:
                for member in sorted(set(hashes) | set(expected_dirs)):
                    path = stage / member
                    tar.add(path, arcname=member, recursive=False)
            verify_archive(archive, hashes, expected_dirs)
            os.chmod(archive, 0o600)  # Protect private contents even outside CLI.
            # No-clobber publication: link fails if destination appeared.
            os.link(archive, target)
            return {
                "archive_sha256": digest(target),
                "archive_bytes": target.stat().st_size,
                "source_files": len(baseline),
                "cutover_eligible": False,
            }
    finally:
        for conn, _ in dbs.values():
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/app/buckets"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--ack-private-plaintext-archive", action="store_true")
    args = parser.parse_args()
    old_umask = os.umask(0o077)
    try:
        validate_source(args.root)
        before = inspect(args.root)
        if not args.create:
            print("READ_ONLY_PREFLIGHT=PASS", "FILES=", len(before),
                  "BYTES=", sum(v[0] for v in before.values()))
            print("BACKUP_CREATED=False")
            return 0
        if not args.ack_private_plaintext_archive or args.output is None:
            raise Refusal("explicit_private_archive_confirmation_required")
        outcome = prebackup(args.root, args.output)
        print("PREBACKUP_CREATED=True")
        for k, v in outcome.items():
            print(k, "=", v)
        print("PRIVATE_ARCHIVE_EXPORT_ENCRYPTION_AND_RETRIEVAL_STILL_REQUIRED=True")
        return 0
    except (Refusal, OSError, sqlite3.Error, ValueError) as exc:
        # Avoid accidentally printing private filenames, config or tokens.
        print("REFUSED=", str(exc) if isinstance(exc, Refusal) else type(exc).__name__)
        return 2
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())

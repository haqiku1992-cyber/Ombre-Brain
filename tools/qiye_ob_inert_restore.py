#!/usr/bin/env python3
"""Verify and optionally stage the Qiye OB prebackup on its private Railway volume.

Default is read-only. --apply restores ONLY to a new private inert staging
directory, never directly to /app/buckets. No provider calls / OB app startup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
from pathlib import Path

ROOT = Path("/app/buckets")
ARCHIVE_NAME = "qiye-ob-prebackup.tar.gz"
DEST_NAME = ".qiye-prebackup-verified-20261025"
EXPECTED_ARCHIVE_SHA = "f51816be535a0b7f4b970336fb2bb8cf4b59d90019564cae9d67c0ec90294501"
EXPECTED_ARCHIVE_BYTES = 5895162
EXPECTED_RAILWAY_SERVICE_ID = "34c3c1ad-b5c0-4cf1-8079-b89fcbefa355"
EXPECTED_SOURCE_PREFIX = "service-6a51f4daf04125ac9a34c5da"
EXPECTED_FILES = 924
MAX_ARCHIVE_FILES = 1500
MAX_TOTAL_EXTRACTED_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
CHUNK = 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class Refusal(Exception):
    pass


def refuse(code):
    raise Refusal(code)


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_name(name):
    if (not isinstance(name, str) or name.startswith("/")
            or chr(92) in name or any(p in ("", ".", "..") for p in name.split("/"))
            or not name.startswith("files/")):
        refuse("unsafe_member_name")


def verify(root, service_id):
    """Return source manifest and safe member catalog; never write."""
    if service_id != EXPECTED_RAILWAY_SERVICE_ID:
        refuse("wrong_railway_service")
    if not root.is_dir() or not os.path.ismount(root):
        refuse("not_dedicated_volume_mount")
    arc = root / ARCHIVE_NAME
    if (not arc.is_file() or arc.is_symlink()
            or arc.stat().st_size != EXPECTED_ARCHIVE_BYTES):
        refuse("unexpected_archive_file")
    if file_digest(arc) != EXPECTED_ARCHIVE_SHA:
        refuse("archive_sha256_mismatch")
    with tarfile.open(arc, mode="r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_ARCHIVE_FILES + 300:
            refuse("too_many_members")
        if sum(m.size for m in members) > MAX_TOTAL_EXTRACTED_BYTES + MAX_MANIFEST_BYTES:
            refuse("oversized_archive")
        mapping = {}
        for member in members:
            if member.name in mapping:
                refuse("duplicate_member")
            if member.name != "manifest.json":
                validate_name(member.name)
            if not (member.isfile() or member.isdir()):
                refuse("unsafe_member_type")
            if member.isfile() and member.size > MAX_TOTAL_EXTRACTED_BYTES:
                refuse("oversized_member")
            mapping[member.name] = member
        header = mapping.get("manifest.json")
        if not header or not header.isfile() or header.size > MAX_MANIFEST_BYTES:
            refuse("missing_manifest")
        stream = tar.extractfile(header)
        if stream is None:
            refuse("missing_manifest_stream")
        manifest = json.load(stream)
        if not isinstance(manifest, dict):
            refuse("invalid_manifest")
        files = manifest.get("files")
        dirs = manifest.get("directories")
        if not isinstance(files, dict) or not isinstance(dirs, dict):
            refuse("invalid_catalog")
        if (manifest.get("schema") != "qiye-ob-prebackup-v1"
                or manifest.get("service_prefix") != EXPECTED_SOURCE_PREFIX
                or manifest.get("overlay_version") != "3.6.13"
                or manifest.get("cutover_eligible") is not False
                or manifest.get("file_count") != EXPECTED_FILES
                or len(files) != EXPECTED_FILES):
            refuse("backup_identity_mismatch")
        if set(mapping) != set(files) | set(dirs) | {"manifest.json"}:
            refuse("catalog_member_mismatch")
        required = {
            "files/config.yaml", "files/embeddings.db",
            "files/dehydration_cache.db", "files/_app/VERSION",
            "files/_app/src/server.py", "files/_app/frontend/dashboard.html"
        }
        if not required.issubset(files):
            refuse("missing_critical_file")
        for name, value in files.items():
            validate_name(name)
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                refuse("invalid_expected_file_hash")
            if not mapping[name].isfile():
                refuse("file_type_mismatch")
        for name, meta in dirs.items():
            validate_name(name)
            if not mapping[name].isdir() or not isinstance(meta, list) or len(meta) != 2:
                refuse("directory_type_or_metadata_mismatch")
            if not all(isinstance(x, int) and x >= 0 for x in meta):
                refuse("invalid_directory_metadata")
        for name in required:
            if mapping[name].size > MAX_TOTAL_EXTRACTED_BYTES:
                refuse("invalid_critical_file")
        readback_version = tar.extractfile(mapping["files/_app/VERSION"]).read(40).strip()
        if readback_version != b"3.6.13":
            refuse("restored_version_mismatch")
        verified = 0
        for name, expected in files.items():
            stream = tar.extractfile(mapping[name])
            if stream is None:
                refuse("unreadable_member")
            h = hashlib.sha256()
            for chunk in iter(lambda: stream.read(CHUNK), b""):
                h.update(chunk)
            if h.hexdigest() != expected:
                refuse("member_sha256_mismatch")
            verified += 1
    return {"file_count": verified, "directory_count": len(dirs), "files": files,
            "directories": dirs}


def restore(root, catalog):
    """Create a NEW staging leaf, inspect DBs, then atomically rename it."""
    dest = root / DEST_NAME
    if dest.exists() or dest.is_symlink():
        refuse("destination_already_exists")
    base = Path(tempfile.mkdtemp(prefix=".qiye-prebackup-incomplete-", dir=root))
    applied = False
    try:
        stage = base / "restored"
        stage.mkdir(mode=0o700)
        # Keep directory structure, including empty folders.
        dirs = catalog["directories"]
        for name in sorted(dirs, key=lambda s: (s.count("/"), s)):
            relative = name.removeprefix("files/")
            (stage / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
        with tarfile.open(root / ARCHIVE_NAME, "r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            for name, expected in catalog["files"].items():
                member = members[name]
                relative = name.removeprefix("files/")
                output = stage / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                reader = tar.extractfile(member)
                if reader is None:
                    refuse("missing_extracted_source")
                h = hashlib.sha256()
                # Exclusive create: never follow/overwrite a path already present.
                fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "wb") as writer:
                    for chunk in iter(lambda: reader.read(CHUNK), b""):
                        writer.write(chunk)
                        h.update(chunk)
                if h.hexdigest() != expected or output.stat().st_size != member.size:
                    refuse("extracted_file_mismatch")
                os.chmod(output, member.mode & 0o777)
                os.utime(output, (member.mtime, member.mtime))
        # Don't write to source databases; restored copies are isolated.
        for name in ("embeddings.db", "dehydration_cache.db"):
            path = stage / name
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
            try:
                connection.execute("PRAGMA query_only=ON")
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    refuse("restored_database_invalid")
            finally:
                connection.close()
        if (stage / "_app/VERSION").read_text(encoding="utf-8").strip() != "3.6.13":
            refuse("restored_code_version_invalid")
        # Preserve archive source-directory permissions and timestamps last.
        for name in sorted(dirs, key=lambda s: (-s.count("/"), s)):
            relative = name.removeprefix("files/")
            path = stage / relative
            mode, mtime_ns = dirs[name]
            os.chmod(path, mode & 0o777)
            os.utime(path, ns=(mtime_ns, mtime_ns))
        # Re-verify restored bytes, excluding directory mtime changes.
        for name, expected in catalog["files"].items():
            if file_digest(stage / name.removeprefix("files/")) != expected:
                refuse("post_restore_digest_mismatch")
        if dest.exists() or dest.is_symlink():
            refuse("destination_appeared")
        # Promote only after every check passes; don't touch existing /app/buckets contents.
        os.rename(base, dest)
        applied = True
        return {"files": len(catalog["files"]), "dbs": 2,
                "relative_staging_path": DEST_NAME + "/restored"}
    finally:
        if not applied and base.exists():
            shutil.rmtree(base)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Restore only into fresh isolated staging leaf.")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        catalog = verify(ROOT, os.getenv("RAILWAY_SERVICE_ID"))
        print("ARCHIVE_VERIFY = PASS")
        print("FILES_VERIFIED =", catalog["file_count"])
        if not args.apply:
            print("RESTORE_APPLIED = False")
            return 0
        outcome = restore(ROOT, catalog)
        print("RESTORE_APPLIED = True")
        print("FILES_RESTORED =", outcome["files"])
        print("DB_QUICK_CHECK = 2/2 PASS")
        print("STAGING_DIR =", outcome["relative_staging_path"])
        print("OB_STARTED = False")
        print("CUTOVER_ELIGIBLE = False")
        return 0
    except (Refusal, OSError, sqlite3.Error, tarfile.TarError, json.JSONDecodeError) as exc:
        print("REFUSED =", str(exc) if isinstance(exc, Refusal) else type(exc).__name__)
        print("RESTORE_APPLIED = False")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

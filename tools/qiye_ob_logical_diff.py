#!/usr/bin/env python3
"""Qiye OB source vs prebackup: in-memory, read-only SQLite logical diff.

Compares schema and row multiset digests, ignoring physical database page
layout and row iteration order. No memory content/table names are printed.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path

ROOT = Path("/app/buckets")
ARCHIVE = Path("/tmp/qiye-ob-prebackup.tar.gz")
SOURCE_PREFIX = "service-6a51f4daf04125ac9a34c5da-"
EXPECTED_ARCHIVE_SHA = "f51816be535a0b7f4b970336fb2bb8cf4b59d90019564cae9d67c0ec90294501"
DBS = ("embeddings.db", "dehydration_cache.db")
CHUNK = 1024 * 1024
MAX_DB_BYTES = 64 * CHUNK


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def require(value, reason):
    if not value:
        raise SystemExit("REFUSED = " + reason)


def snapshot_source(path):
    require(path.is_file() and not path.is_symlink(), "missing_or_symlink_source_db")
    require(path.stat().st_size < MAX_DB_BYTES, "source_db_too_large")
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    dest = sqlite3.connect(":memory:")
    try:
        source.backup(dest, pages=100, sleep=0.05)
        dest.execute("PRAGMA query_only=ON")
        require(dest.execute("PRAGMA quick_check").fetchone() == ("ok",),
                "source_sqlite_quick_check_failed")
        return dest
    except BaseException:
        dest.close()
        raise
    finally:
        source.close()


def archived_snapshot(tar, manifest, filename):
    key = "files/" + filename
    require(key in manifest["files"], "missing_manifest_db")
    member = tar.getmember(key)
    require(member.isfile() and member.size < MAX_DB_BYTES,
            "unexpected_db_member")
    stream = tar.extractfile(member)
    require(stream is not None, "unreadable_db_member")
    data = stream.read(MAX_DB_BYTES + 1)
    require(len(data) == member.size, "bad_db_member_length")
    require(hashlib.sha256(data).hexdigest() == manifest["files"][key],
            "archived_db_hash_mismatch")
    require(hasattr(sqlite3.Connection, "deserialize"),
            "sqlite_deserialize_unavailable")
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(data)
        db.execute("PRAGMA query_only=ON")
        require(db.execute("PRAGMA quick_check").fetchone() == ("ok",),
                "archived_sqlite_quick_check_failed")
        return db
    except BaseException:
        db.close()
        raise


def schema_info(db):
    records = list(db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
    ))
    schema = tuple(tuple(r) for r in records)
    physical = set()
    virtual = set()
    for kind, name, _, sql in records:
        if kind != "table":
            continue
        if (sql or "").lstrip().upper().startswith("CREATE VIRTUAL TABLE"):
            virtual.add(name)
        else:
            physical.add(name)
    return schema, physical, virtual


def serialize_row(row):
    parts = []
    for val in row:
        if val is None:
            kind, raw = b"N", b""
        elif isinstance(val, int):
            kind, raw = b"I", str(val).encode("ascii")
        elif isinstance(val, float):
            kind, raw = b"F", val.hex().encode("ascii")
        elif isinstance(val, str):
            kind, raw = b"T", val.encode("utf-8", "surrogatepass")
        elif isinstance(val, (bytes, bytearray, memoryview)):
            kind, raw = b"B", bytes(val)
        else:
            require(False, "unsupported_sqlite_value_type")
        parts.append(kind + len(raw).to_bytes(8, "big") + raw)
    return b"".join(parts)


def table_signature(db, table):
    name = '"' + table.replace('"', '""') + '"'
    hashes = [hashlib.sha256(serialize_row(row)).digest()
              for row in db.execute("SELECT * FROM " + name)]
    hashes.sort()
    h = hashlib.sha256()
    for digest in hashes:
        h.update(digest)
    return len(hashes), h.digest()


def compare_databases(current, archived):
    schema_now, physical_now, virtual_now = schema_info(current)
    schema_old, physical_old, virtual_old = schema_info(archived)
    schema_match = schema_now == schema_old
    commons = physical_now & physical_old
    changed = sum(table_signature(current, name) != table_signature(archived, name)
                  for name in sorted(commons))
    missing = len(physical_old - physical_now)
    added = len(physical_now - physical_old)
    return {
        "SCHEMA_MATCH": schema_match,
        "PHYSICAL_TABLES_COMPARED": len(commons),
        "PHYSICAL_TABLES_CHANGED": changed,
        "PHYSICAL_TABLES_ADDED": added,
        "PHYSICAL_TABLES_REMOVED": missing,
        "VIRTUAL_TABLES_PRESENT": len(virtual_now | virtual_old),
        "LOGICAL_ROWS_MATCH": schema_match and changed == missing == added == 0,
    }


def main():
    require(socket.gethostname().startswith(SOURCE_PREFIX),
            "wrong_zeabur_service")
    require(os.path.ismount(ROOT), "wrong_source_volume")
    require(ARCHIVE.is_file() and not ARCHIVE.is_symlink(),
            "source_archive_missing")
    require(sha256(ARCHIVE) == EXPECTED_ARCHIVE_SHA,
            "archive_hash_mismatch")
    with tarfile.open(ARCHIVE, "r:gz") as tar:
        entry = tar.getmember("manifest.json")
        require(entry.isfile() and entry.size < CHUNK, "manifest_invalid")
        reader = tar.extractfile(entry)
        require(reader is not None, "manifest_unreadable")
        manifest = json.load(reader)
        require(
            manifest.get("schema") == "qiye-ob-prebackup-v1"
            and manifest.get("service_prefix") == SOURCE_PREFIX.rstrip("-")
            and manifest.get("overlay_version") == "3.6.13"
            and manifest.get("cutover_eligible") is False
            and manifest.get("file_count") == 924,
            "wrong_manifest_identity"
        )
        print("SOURCE_IDENTITY = PASS")
        print("ARCHIVE_SHA256 = PASS")
        for filename in DBS:
            with closing(snapshot_source(ROOT / filename)) as current:
                with closing(archived_snapshot(tar, manifest, filename)) as archived:
                    result = compare_databases(current, archived)
            print("DATABASE =", filename)
            for key, value in result.items():
                print(key, "=", value)
    print("AUDIT_READ_ONLY = True")
    print("FINAL_WRITE_FREEZE_DONE = False")


if __name__ == "__main__":
    main()

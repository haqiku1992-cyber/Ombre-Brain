"""Offline synthetic archive tests for Qiye Railway inert restore."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

FILE = Path(__file__).resolve().parents[1] / "tools" / "qiye_ob_inert_restore.py"
spec = importlib.util.spec_from_file_location("qiye_inert", FILE)
assert spec and spec.loader
inert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inert)


class QiyeInertRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "buckets"
        self.root.mkdir()
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()
        for path in ("_app/src", "_app/frontend"):
            (self.source / path).mkdir(parents=True)
        (self.source / "_app/VERSION").write_text("3.6.13\n")
        (self.source / "_app/src/server.py").write_text("synthetic program")
        (self.source / "_app/frontend/dashboard.html").write_text("fake dashboard")
        (self.source / "config.yaml").write_text("synthetic_only: true\n")
        for name in ("embeddings.db", "dehydration_cache.db"):
            with sqlite3.connect(self.source / name) as db:
                db.execute("CREATE TABLE fixture (v INTEGER)")
                db.execute("INSERT INTO fixture VALUES (17)")
        self.names = tuple([
            "config.yaml", "_app/VERSION", "_app/src/server.py",
            "_app/frontend/dashboard.html", "embeddings.db",
            "dehydration_cache.db",
        ])
        self.dirs = ("_app", "_app/src", "_app/frontend")
        self.patch_mount = mock.patch.object(
            inert.os.path, "ismount", side_effect=lambda path: Path(path) == self.root
        )
        self.patch_mount.start()
        self.addCleanup(self.patch_mount.stop)
        self.old_count = inert.EXPECTED_FILES
        inert.EXPECTED_FILES = len(self.names)
        self.addCleanup(setattr, inert, "EXPECTED_FILES", self.old_count)
        self.prepare()

    def prepare(self, *, mutate=None):
        archive = self.root / inert.ARCHIVE_NAME
        hashes = {}
        for name in self.names:
            data = (self.source / name).read_bytes()
            hashes["files/" + name] = hashlib.sha256(data).hexdigest()
        if mutate == "wrong_digest":
            hashes["files/config.yaml"] = "0" * 64
        manifest = {
            "schema": "qiye-ob-prebackup-v1",
            "service_prefix": inert.EXPECTED_SOURCE_PREFIX,
            "overlay_version": "3.6.13",
            "cutover_eligible": False,
            "file_count": len(self.names),
            "files": hashes,
            "directories": {"files/" + name: [0o755, 1_700_000_000_000_000_000]
                            for name in self.dirs},
        }
        with tarfile.open(archive, "w:gz") as tar:
            for d in self.dirs:
                ti = tarfile.TarInfo("files/" + d)
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                ti.mtime = 1700000000
                tar.addfile(ti)
            for name in self.names:
                data = (self.source / name).read_bytes()
                ti = tarfile.TarInfo("files/" + name)
                ti.size = len(data)
                ti.mode = 0o600
                ti.mtime = 1700000000
                tar.addfile(ti, io.BytesIO(data))
            mb = json.dumps(manifest).encode()
            ti = tarfile.TarInfo("manifest.json")
            ti.size = len(mb)
            tar.addfile(ti, io.BytesIO(mb))
            if mutate == "duplicate":
                ti = tarfile.TarInfo("files/config.yaml")
                ti.size = 1
                tar.addfile(ti, io.BytesIO(b"x"))
            if mutate == "path_traversal":
                ti = tarfile.TarInfo("files/../escape")
                ti.size = 1
                tar.addfile(ti, io.BytesIO(b"x"))
        self.archive = archive
        self.patch_sha = mock.patch.object(inert, "EXPECTED_ARCHIVE_SHA", inert.file_digest(archive))
        self.patch_size = mock.patch.object(inert, "EXPECTED_ARCHIVE_BYTES", archive.stat().st_size)
        if hasattr(self, "current_patches"):
            for patcher in self.current_patches:
                patcher.stop()
        self.current_patches = [self.patch_sha, self.patch_size]
        for patcher in self.current_patches:
            patcher.start()
        self.addCleanup(self.cleanup_patches)

    def cleanup_patches(self):
        for patcher in self.current_patches:
            patcher.stop()

    def catalog(self):
        return inert.verify(self.root, inert.EXPECTED_RAILWAY_SERVICE_ID)

    def test_verified_archive_is_read_only(self):
        catalog = self.catalog()
        self.assertEqual(catalog["file_count"], 6)
        self.assertFalse((self.root / inert.DEST_NAME).exists())

    def test_staged_restore_keeps_archive_and_db_valid(self):
        catalog = self.catalog()
        outcome = inert.restore(self.root, catalog)
        self.assertEqual(outcome["files"], 6)
        self.assertEqual(outcome["dbs"], 2)
        final = self.root / inert.DEST_NAME / "restored"
        self.assertEqual((final / "_app/VERSION").read_text().strip(), "3.6.13")
        self.assertTrue(self.archive.is_file())
        with sqlite3.connect(final / "embeddings.db") as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT v FROM fixture").fetchone()[0], 17)
        self.assertFalse((self.root / "embeddings.db").exists())

    def test_wrong_railway_service_fails(self):
        with self.assertRaisesRegex(inert.Refusal, "wrong_railway_service"):
            inert.verify(self.root, "shadow-service")

    def test_corrupted_download_refused(self):
        self.archive.write_bytes(self.archive.read_bytes() + b"x")
        with self.assertRaisesRegex(inert.Refusal, "unexpected_archive_file"):
            self.catalog()

    def test_checksum_mismatch_refused(self):
        self.prepare(mutate="wrong_digest")
        with self.assertRaisesRegex(inert.Refusal, "member_sha256_mismatch"):
            self.catalog()

    def test_duplicate_member_refused(self):
        self.prepare(mutate="duplicate")
        with self.assertRaisesRegex(inert.Refusal, "duplicate_member"):
            self.catalog()

    def test_path_traversal_refused(self):
        self.prepare(mutate="path_traversal")
        with self.assertRaisesRegex(inert.Refusal, "unsafe_member_name"):
            self.catalog()
        self.assertFalse((self.root.parent / "escape").exists())

    def test_existing_target_refused_no_overwrite(self):
        dest = self.root / inert.DEST_NAME
        dest.mkdir()
        sentinel = dest / "keep"
        sentinel.write_text("old")
        with self.assertRaisesRegex(inert.Refusal, "destination_already_exists"):
            inert.restore(self.root, self.catalog())
        self.assertEqual(sentinel.read_text(), "old")

    def test_broken_database_refused_and_unpublished(self):
        (self.source / "embeddings.db").write_bytes(b"not sqlite")
        self.prepare()
        with self.assertRaises(sqlite3.DatabaseError):
            inert.restore(self.root, self.catalog())
        self.assertFalse((self.root / inert.DEST_NAME).exists())
        self.assertEqual(
            list(self.root.glob(".qiye-prebackup-incomplete-*")), []
        )


if __name__ == "__main__":
    unittest.main()

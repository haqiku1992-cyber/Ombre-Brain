"""Synthetic, network-free safety tests for Qiye OB prebackup."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / "tools" / "qiye_ob_prebackup.py"
SPEC = importlib.util.spec_from_file_location("qiye_ob_prebackup", PATH)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class QiyePrebackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "buckets"
        self.outdir = self.base / "exports"
        self.root.mkdir()
        self.outdir.mkdir()
        (self.root / "_app/src").mkdir(parents=True)
        (self.root / "_app/frontend").mkdir(parents=True)
        (self.root / "_app/VERSION").write_text("3.6.13\n")
        (self.root / "_app/src/server.py").write_text("original restored app")
        (self.root / "_app/frontend/dashboard.html").write_text("restored dashboard")
        (self.root / "config.yaml").write_text("private: fixture-not-real\n")
        (self.root / "one.md").write_text("not a real memory\n")
        for name in backup.DB_NAMES:
            with sqlite3.connect(self.root / name) as db:
                db.execute("CREATE TABLE records (item TEXT)")
                db.execute("INSERT INTO records VALUES ('fixture')")

        self.hostname = mock.patch.object(
            backup.socket, "gethostname",
            return_value=backup.SERVICE_PREFIX + "-fake-container",
        )
        self.ismount = mock.patch.object(
            backup.os.path, "ismount", side_effect=lambda p: Path(p) == self.root
        )
        self.hostname.start()
        self.ismount.start()
        self.addCleanup(self.hostname.stop)
        self.addCleanup(self.ismount.stop)

    def output(self, name="backup.tar.gz"):
        return self.outdir / name

    def test_archive_is_complete_private_and_not_cutover(self):
        out = self.output()
        result = backup.prebackup(self.root, out)
        self.assertFalse(result["cutover_eligible"])
        self.assertTrue(out.exists())
        self.assertEqual(os.stat(out).st_mode & 0o077, 0)
        with tarfile.open(out, "r:gz") as tar:
            manifest = json.load(tar.extractfile("manifest.json"))
            self.assertFalse(manifest["cutover_eligible"])
            self.assertEqual(len(manifest["files"]), result["source_files"])
            self.assertIn("files/config.yaml", manifest["files"])
            self.assertIn("files/_app/src/server.py", manifest["files"])
            self.assertEqual(
                tar.extractfile("files/one.md").read(), b"not a real memory\n"
            )
            # Separate in-memory proof that a backed-up DB opens and retains rows.
            data = tar.extractfile("files/embeddings.db").read()
        db_path = self.base / "roundtrip.db"
        db_path.write_bytes(data)
        with sqlite3.connect(db_path) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT item FROM records").fetchone()[0], "fixture")
        self.assertTrue((self.root / "embeddings.db").exists())

    def test_hostname_guard_fails_before_source_read(self):
        with mock.patch.object(backup.socket, "gethostname", return_value="wrong"):
            with self.assertRaisesRegex(backup.Refusal, "wrong_zeabur_service"):
                backup.prebackup(self.root, self.output())
        self.assertFalse(self.output().exists())

    def test_wrong_overlay_version_refused(self):
        (self.root / "_app/VERSION").write_text("3.6.14\n")
        with self.assertRaisesRegex(backup.Refusal, "runtime_version_mismatch"):
            backup.prebackup(self.root, self.output())

    def test_symlink_refused(self):
        (self.root / "sneaky.md").symlink_to(self.base / "outside.txt")
        with self.assertRaisesRegex(backup.Refusal, "unsafe_file_type"):
            backup.prebackup(self.root, self.output())

    def test_unapproved_sqlite_refused(self):
        with sqlite3.connect(self.root / "extra.db") as db:
            db.execute("CREATE TABLE unexpected (x INTEGER)")
        with self.assertRaisesRegex(backup.Refusal, "unapproved_database"):
            backup.prebackup(self.root, self.output())

    def test_orphan_sidecar_refused(self):
        (self.root / "embeddings.db-wal").write_text("fake")
        with self.assertRaisesRegex(backup.Refusal, "sidecar_or_duplicate"):
            backup.prebackup(self.root, self.output())

    def test_will_not_overwrite_existing_archive(self):
        self.output().write_bytes(b"unchanged")
        with self.assertRaisesRegex(backup.Refusal, "target_must_be_new"):
            backup.prebackup(self.root, self.output())
        self.assertEqual(self.output().read_bytes(), b"unchanged")

    def test_changed_static_file_discarded(self):
        original = backup.copy_one
        changed = False

        def concurrent_write(src, dst, sha):
            nonlocal changed
            original(src, dst, sha)
            if not changed and src.name == "one.md":
                changed = True
                src.write_text("changed during backup")

        with mock.patch.object(backup, "copy_one", side_effect=concurrent_write):
            with self.assertRaisesRegex(backup.Refusal, "source_changed_during_backup"):
                backup.prebackup(self.root, self.output())
        self.assertFalse(self.output().exists())

    def test_empty_directories_and_metadata_survive(self):
        empty_dir = self.root / "unused" / "empty"
        empty_dir.mkdir(parents=True)
        os.chmod(empty_dir, 0o750)
        source_file = self.root / "one.md"
        os.chmod(source_file, 0o640)
        source_time_ns = 1_700_000_000_000_000_000
        os.utime(source_file, ns=(source_time_ns, source_time_ns))
        out = self.output()
        backup.prebackup(self.root, out)
        with tarfile.open(out, "r:gz") as tar:
            files = {m.name: m for m in tar.getmembers()}
            self.assertTrue(files["files/unused/empty"].isdir())
            self.assertEqual(files["files/unused/empty"].mode, 0o750)
            self.assertEqual(files["files/one.md"].mode, 0o640)
            self.assertAlmostEqual(
                files["files/one.md"].mtime,
                source_time_ns / 1_000_000_000,
                delta=1.0,
            )
            manifest = json.load(tar.extractfile("manifest.json"))
            self.assertIn("files/unused/empty", manifest["directories"])

    def test_directory_changes_during_copy_fail_closed(self):
        original = backup.copy_one
        changed = False

        def concurrent_directory_change(src, dst, sha):
            nonlocal changed
            original(src, dst, sha)
            if not changed and src.name == "one.md":
                changed = True
                (self.root / "new_empty_dir").mkdir()

        with mock.patch.object(backup, "copy_one", side_effect=concurrent_directory_change):
            with self.assertRaisesRegex(backup.Refusal, "source_directories_changed"):
                backup.prebackup(self.root, self.output())
        self.assertFalse(self.output().exists())

    def test_low_disk_capacity_rejected_without_artifact(self):
        from types import SimpleNamespace
        no_space = SimpleNamespace(f_bavail=0, f_frsize=4096, f_favail=1000000)
        with mock.patch.object(backup.os, "statvfs", return_value=no_space):
            with self.assertRaisesRegex(
                backup.Refusal, "insufficient_private_staging_capacity"
            ):
                backup.prebackup(self.root, self.output())
        self.assertFalse(self.output().exists())

    def test_invalid_timeout_refused(self):
        with self.assertRaisesRegex(backup.Refusal, "invalid_max_seconds"):
            with backup.bounded_capture(1):
                pass

    def test_archive_verification_error_leaves_no_published_file(self):
        with mock.patch.object(
            backup, "verify_archive", side_effect=backup.Refusal("failed_archive")
        ):
            with self.assertRaisesRegex(backup.Refusal, "failed_archive"):
                backup.prebackup(self.root, self.output())
        self.assertFalse(self.output().exists())

    def test_source_mount_required(self):
        with mock.patch.object(backup.os.path, "ismount", return_value=False):
            with self.assertRaisesRegex(backup.Refusal, "volume_mount_required"):
                backup.prebackup(self.root, self.output())


if __name__ == "__main__":
    unittest.main()

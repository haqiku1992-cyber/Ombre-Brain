"""Offline, synthetic tests for the Railway restored-code entrypoint fence."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / "railway_restore_entrypoint.sh"


class RestoreEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_python = fake_bin / "python"
        fake_python.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$MOCK_PY_ARGS"\n',
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
        self.marker = self.root / "python_args"
        self.config = self.root / "buckets" / "config.yaml"
        self.code = self.root / "buckets" / "_app"
        self.env = dict(os.environ)
        self.env.update({
            "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            "MOCK_PY_ARGS": str(self.marker),
            "OMBRE_CONFIG_PATH": str(self.config),
            "OMBRE_CODE_DIR": str(self.code),
        })

    def run_entrypoint(self, mode, *, expected=None):
        env = dict(self.env)
        env["OMBRE_RAILWAY_RESTORE_MODE"] = mode
        return subprocess.run(
            ["sh", str(ENTRYPOINT)],
            cwd=self.root, env=env, text=True, capture_output=True, timeout=5,
            check=False,
        )

    def populate_restored_code(self, version="3.6.14"):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text("original_config: keep\n", encoding="utf-8")
        (self.code / "src").mkdir(parents=True)
        (self.code / "frontend").mkdir()
        (self.code / "src/server.py").write_text("# original restored server\n")
        (self.code / "frontend/dashboard.html").write_text("original dashboard")
        (self.code / "VERSION").write_text(version + "\n")
        (self.code / ".seeded_image_version").write_text("2.4.15\n")
        (self.code / ".seeded_image_hash").write_text("old-seeded-hash\n")

    def test_wait_ignores_uninitialized_volume_and_does_not_start_ob(self):
        result = self.run_entrypoint("wait")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.config.parent.exists())
        self.assertEqual(self.marker.read_text().splitlines()[0], "-c")
        self.assertIn("threading.Event().wait()", self.marker.read_text())

    def test_preserve_runs_restored_version_without_reseed_or_boot_counter(self):
        self.populate_restored_code()
        before = {
            name: (self.code / name).read_bytes()
            for name in ("src/server.py", "frontend/dashboard.html", "VERSION",
                         ".seeded_image_version", ".seeded_image_hash")
        }
        result = self.run_entrypoint("app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text().strip(), "src/server.py")
        for name, original in before.items():
            self.assertEqual((self.code / name).read_bytes(), original, name)
        self.assertFalse((self.code / ".boot_fails").exists())

    def test_preserve_refuses_wrong_version(self):
        self.populate_restored_code(version="2.4.15")
        result = self.run_entrypoint("app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected restored code v3.6.14", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual((self.code / "VERSION").read_text().strip(), "2.4.15")

    def test_preserve_refuses_missing_files(self):
        self.populate_restored_code()
        target = self.code / "src/server.py"
        target.rename(target.with_suffix(".hold"))
        result = self.run_entrypoint("app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restored data/code incomplete", result.stderr)
        self.assertFalse(self.marker.exists())
        target.with_suffix(".hold").rename(target)

    def test_unknown_mode_refused_without_writes(self):
        result = self.run_entrypoint("unexpected")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown OMBRE_RAILWAY_RESTORE_MODE", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.config.parent.exists())


if __name__ == "__main__":
    unittest.main()

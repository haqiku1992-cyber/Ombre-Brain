"""Synthetic, network-free checks for the 栖野 Railway OB WAIT gate."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "railway_qiye_restore_entrypoint.sh"


class QiyeRestoreGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake_python = self.bin / "python"
        fake_python.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$MOCK_PY_ARGS"\n'
        )
        fake_python.chmod(0o755)
        self.args = self.root / "python_args"
        self.buckets = self.root / "buckets"
        self.code = self.buckets / "_app"
        self.env = dict(os.environ)
        self.env.update({
            "PATH": str(self.bin) + os.pathsep + self.env.get("PATH", ""),
            "MOCK_PY_ARGS": str(self.args),
            "OMBRE_BUCKETS_DIR": str(self.buckets),
            "OMBRE_CONFIG_PATH": str(self.buckets / "config.yaml"),
            "OMBRE_CODE_DIR": str(self.code),
            "OMBRE_QIYE_IDENTITY": "qiye",
            "OMBRE_QIYE_RESTORE_VERIFIED": "1",
        })

    def restore_files(self, version="3.6.13"):
        (self.code / "src").mkdir(parents=True)
        (self.code / "frontend").mkdir()
        (self.code / "VERSION").write_text(version + "\n")
        (self.code / "src/server.py").write_text("original server")
        (self.code / "frontend/dashboard.html").write_text("original dashboard")
        (self.buckets / "config.yaml").write_text("original config")
        (self.code / ".seeded_image_version").write_text("2.4.15")

    def run_guard(self, mode="wait", **extra):
        env = dict(self.env)
        env["OMBRE_RAILWAY_RESTORE_MODE"] = mode
        env.update(extra)
        return subprocess.run(
            ["sh", str(SCRIPT)], env=env, cwd=self.root,
            capture_output=True, text=True, timeout=5, check=False,
        )

    def test_wait_never_creates_volume_or_starts_server(self):
        r = self.run_guard()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.buckets.exists())
        self.assertIn("threading.Event().wait()", self.args.read_text())

    def test_app_uses_restored_code_without_reseed(self):
        self.restore_files()
        before = {
            p: (self.code / p).read_bytes()
            for p in ("VERSION", "src/server.py", "frontend/dashboard.html",
                      ".seeded_image_version")
        }
        r = self.run_guard(mode="app")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.args.read_text().strip(), "src/server.py")
        for p, contents in before.items():
            self.assertEqual((self.code / p).read_bytes(), contents)

    def test_missing_approval_refuses(self):
        self.restore_files()
        r = self.run_guard(mode="app", OMBRE_QIYE_RESTORE_VERIFIED="0")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.args.exists())

    def test_wrong_identity_refuses(self):
        self.restore_files()
        r = self.run_guard(mode="app", OMBRE_QIYE_IDENTITY="xinzhi")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.args.exists())

    def test_wrong_version_refuses(self):
        self.restore_files("3.6.14")
        r = self.run_guard(mode="app")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("expected restored code v3.6.13", r.stderr)
        self.assertFalse(self.args.exists())

    def test_missing_config_refuses(self):
        self.restore_files()
        (self.buckets / "config.yaml").unlink()
        self.assertNotEqual(self.run_guard(mode="app").returncode, 0)
        self.assertFalse(self.args.exists())

    def test_unknown_mode_refuses(self):
        self.assertNotEqual(self.run_guard(mode="danger").returncode, 0)
        self.assertFalse(self.args.exists())


if __name__ == "__main__":
    unittest.main()

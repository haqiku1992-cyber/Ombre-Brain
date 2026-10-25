"""Ensure the Qiye Railway image cannot accidentally execute upstream reseeding."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile.railway-qiye-restore"
WRAPPER = ROOT / "railway_qiye_restore_entrypoint.sh"
INDEX_SHA = "3284dd935f3a6ad1b819f9b2e5a92033d58b962871548988b1460831929f211e"


class QiyePinnedDockerfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        cls.shell = WRAPPER.read_text(encoding="utf-8")

    def test_base_image_pinned_to_verified_3_6_13_digest(self):
        base = [line.strip() for line in self.dockerfile.splitlines()
                if line.lstrip().startswith("FROM ")]
        self.assertEqual(base, [
            "FROM p0luz/ombre-brain:3.6.13@sha256:" + INDEX_SHA
        ])

    def test_entrypoint_is_qiye_wrapper_not_upstream(self):
        self.assertIn(
            'ENTRYPOINT ["/usr/local/bin/railway-qiye-restore-entrypoint"]',
            self.dockerfile,
        )
        self.assertIn(
            "COPY railway_qiye_restore_entrypoint.sh "
            "/usr/local/bin/railway-qiye-restore-entrypoint",
            self.dockerfile,
        )
        self.assertNotIn("RUN /app/entrypoint.sh", self.dockerfile)
        self.assertNotIn('ENTRYPOINT ["/app/entrypoint.sh"]', self.dockerfile)

    def test_mode_is_wait_by_default(self):
        self.assertIn("ENV OMBRE_RAILWAY_RESTORE_MODE=wait", self.dockerfile)
        self.assertIn('case "${OMBRE_RAILWAY_RESTORE_MODE:-wait}" in', self.shell)
        self.assertIn('if [ "${OMBRE_QIYE_IDENTITY:-}" != "qiye" ]', self.shell)
        self.assertIn('[ "${OMBRE_QIYE_RESTORE_VERIFIED:-}" != "1" ]', self.shell)
        self.assertIn('expected restored code v3.6.13', self.shell)
        self.assertNotIn('exec /app/entrypoint.sh', self.shell)


if __name__ == "__main__":
    unittest.main()

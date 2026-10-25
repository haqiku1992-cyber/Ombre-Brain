"""Unit tests for Qiye source/prebackup logical SQLite comparison."""
from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "tools" / "qiye_ob_logical_diff.py"
spec = importlib.util.spec_from_file_location("qiye_logical_diff", FILE)
assert spec and spec.loader
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def make_database(rows):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE memories (id INTEGER, text BLOB)")
    connection.executemany("INSERT INTO memories VALUES (?,?)", rows)
    connection.commit()
    return connection


class LogicalComparisonTests(unittest.TestCase):
    def test_identical_sqlite_logical_rows(self):
        with make_database([(1, b"alpha"), (2, b"beta")]) as before:
            with sqlite3.connect(":memory:") as after:
                after.deserialize(before.serialize())
                result = tool.compare_databases(before, after)
                self.assertTrue(result["SCHEMA_MATCH"])
                self.assertTrue(result["LOGICAL_ROWS_MATCH"])
                self.assertEqual(result["PHYSICAL_TABLES_CHANGED"], 0)

    def test_reordered_rows_with_same_content_compare_equal(self):
        with make_database([(1, b"alpha"), (2, b"beta"), (2, b"beta")]) as before:
            with make_database([(2, b"beta"), (1, b"alpha"), (2, b"beta")]) as after:
                result = tool.compare_databases(before, after)
                self.assertTrue(result["LOGICAL_ROWS_MATCH"])

    def test_changed_blob_is_detected_without_disclosing_contents(self):
        with make_database([(1, b"alpha"), (2, b"beta")]) as before:
            with make_database([(1, b"alpha"), (2, b"new")]) as after:
                result = tool.compare_databases(before, after)
                self.assertFalse(result["LOGICAL_ROWS_MATCH"])
                self.assertEqual(result["PHYSICAL_TABLES_CHANGED"], 1)
                self.assertNotIn("alpha", str(result))
                self.assertNotIn("beta", str(result))

    def test_added_row_and_duplicate_changes_detected(self):
        with make_database([(1, b"alpha")]) as before:
            with make_database([(1, b"alpha"), (1, b"alpha")]) as after:
                self.assertFalse(tool.compare_databases(before, after)["LOGICAL_ROWS_MATCH"])

    def test_schema_addition_detected(self):
        with make_database([(1, b"alpha")]) as before:
            with make_database([(1, b"alpha")]) as after:
                after.execute("CREATE TABLE additional (id INTEGER)")
                result = tool.compare_databases(before, after)
                self.assertFalse(result["LOGICAL_ROWS_MATCH"])
                self.assertFalse(result["SCHEMA_MATCH"])
                self.assertEqual(result["PHYSICAL_TABLES_ADDED"], 1)


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.gaming_csv_to_db import DEFAULT_DB_PATH
from src.pipeline import SQLValidator


class TestSQLValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = SQLValidator(db_path=DEFAULT_DB_PATH)

    def test_rejects_delete(self):
        result = self.validator.validate("DELETE FROM gaming_mental_health")
        self.assertFalse(result.is_valid)
        self.assertIn("DELETE", result.error)

    def test_rejects_drop(self):
        result = self.validator.validate("DROP TABLE gaming_mental_health")
        self.assertFalse(result.is_valid)

    def test_rejects_update(self):
        result = self.validator.validate("UPDATE gaming_mental_health SET age = 0")
        self.assertFalse(result.is_valid)

    def test_rejects_insert(self):
        result = self.validator.validate("INSERT INTO gaming_mental_health VALUES (1)")
        self.assertFalse(result.is_valid)

    def test_rejects_none_sql(self):
        result = self.validator.validate(None)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error, "No SQL provided")

    def test_accepts_valid_select(self):
        result = self.validator.validate(
            "SELECT age, gender FROM gaming_mental_health LIMIT 10"
        )
        self.assertTrue(result.is_valid)
        self.assertIsNotNone(result.validated_sql)

    def test_accepts_aggregate_query(self):
        result = self.validator.validate(
            "SELECT AVG(anxiety_score) FROM gaming_mental_health"
        )
        self.assertTrue(result.is_valid)

    def test_accepts_group_by(self):
        result = self.validator.validate(
            "SELECT gender, COUNT(*) FROM gaming_mental_health GROUP BY gender"
        )
        self.assertTrue(result.is_valid)

    def test_rejects_invalid_column(self):
        result = self.validator.validate(
            "SELECT zodiac_sign FROM gaming_mental_health"
        )
        self.assertFalse(result.is_valid)
        self.assertIn("syntax error", result.error.lower())

    def test_rejects_invalid_table(self):
        result = self.validator.validate("SELECT age FROM nonexistent_table")
        self.assertFalse(result.is_valid)

    def test_timing_is_set(self):
        result = self.validator.validate("SELECT 1")
        self.assertGreaterEqual(result.timing_ms, 0.0)


if __name__ == "__main__":
    unittest.main()

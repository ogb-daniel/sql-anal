
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.llm_client import OpenRouterLLMClient


class TestExtractSQL(unittest.TestCase):
    def test_json_with_sql(self):
        text = '{"sql": "SELECT age FROM gaming_mental_health", "answerable": true}'
        sql, answerable = OpenRouterLLMClient._extract_sql(text)
        self.assertEqual(sql, "SELECT age FROM gaming_mental_health")
        self.assertTrue(answerable)

    def test_json_unanswerable(self):
        text = '{"sql": null, "answerable": false}'
        sql, answerable = OpenRouterLLMClient._extract_sql(text)
        self.assertIsNone(sql)
        self.assertFalse(answerable)

    def test_plain_select(self):
        sql, answerable = OpenRouterLLMClient._extract_sql("SELECT age FROM t")
        self.assertEqual(sql, "SELECT age FROM t")
        self.assertTrue(answerable)

    def test_select_with_prefix_text(self):
        text = "Here is the SQL query:\nSELECT age FROM t LIMIT 10"
        sql, answerable = OpenRouterLLMClient._extract_sql(text)
        self.assertTrue(sql.startswith("SELECT"))
        self.assertTrue(answerable)

    def test_strips_trailing_semicolon(self):
        text = '{"sql": "SELECT 1;", "answerable": true}'
        sql, answerable = OpenRouterLLMClient._extract_sql(text)
        self.assertFalse(sql.endswith(";"))

    def test_handles_multiple_statements(self):
        text = '{"sql": "SELECT 1; SELECT 2", "answerable": true}'
        sql, answerable = OpenRouterLLMClient._extract_sql(text)
        self.assertEqual(sql, "SELECT 1")

    def test_returns_none_for_nonsql(self):
        sql, answerable = OpenRouterLLMClient._extract_sql("I cannot help with that")
        self.assertIsNone(sql)
        self.assertFalse(answerable)

    def test_empty_string(self):
        sql, answerable = OpenRouterLLMClient._extract_sql("")
        self.assertIsNone(sql)
        self.assertFalse(answerable)


if __name__ == "__main__":
    unittest.main()

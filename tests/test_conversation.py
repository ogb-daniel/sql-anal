"""Unit tests for conversation session — no LLM calls needed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.conversation import ConversationSession


class TestConversationSession(unittest.TestCase):
    def test_empty_session_has_no_history(self):
        session = ConversationSession()
        self.assertFalse(session.has_history())
        self.assertEqual(session.get_context_prompt(), "")

    def test_add_turn_creates_history(self):
        session = ConversationSession()
        session.add_turn("What is avg age?", "SELECT AVG(age) FROM t", "The average age is 25.", [{"avg": 25}])
        self.assertTrue(session.has_history())
        self.assertEqual(len(session.turns), 1)

    def test_context_prompt_includes_history(self):
        session = ConversationSession()
        session.add_turn("Q1?", "SELECT 1", "Answer 1", [{"a": 1}])
        session.add_turn("Q2?", "SELECT 2", "Answer 2", [{"b": 2}])
        prompt = session.get_context_prompt()
        self.assertIn("Q1:", prompt)
        self.assertIn("Q2:", prompt)
        self.assertIn("Answer 1", prompt)
        self.assertIn("SELECT 1", prompt)

    def test_max_history_trims_old_turns(self):
        session = ConversationSession(max_history=2)
        for i in range(5):
            session.add_turn(f"Q{i}", f"SQL{i}", f"A{i}", [])
        self.assertEqual(len(session.turns), 2)
        self.assertEqual(session.turns[0].question, "Q3")
        self.assertEqual(session.turns[1].question, "Q4")

    def test_session_id_is_generated(self):
        s1 = ConversationSession()
        s2 = ConversationSession()
        self.assertNotEqual(s1.session_id, s2.session_id)

    def test_unanswerable_turn_stores_none_sql(self):
        session = ConversationSession()
        session.add_turn("Zodiac?", None, "Cannot answer", [])
        self.assertIsNone(session.turns[0].sql)
        prompt = session.get_context_prompt()
        self.assertNotIn("SQL1:", prompt)  


if __name__ == "__main__":
    unittest.main()

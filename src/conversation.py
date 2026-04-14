from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ConversationTurn:
    question: str
    sql: str | None
    answer: str
    rows_summary: str

@dataclass
class ConversationSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turns: list[ConversationTurn] = field(default_factory=list)
    max_history: int = 5
    def add_turn(self, question: str, sql: str | None, answer: str, rows: list[dict]) -> None:
        summary = self._summarize_rows(rows)
        self.turns.append(ConversationTurn(question, sql, answer, summary))
        if len(self.turns) > self.max_history:
            self.turns = self.turns[-self.max_history:]
    def get_context_prompt(self) -> str:
        if not self.turns:
            return ""
        parts = ["Previous conversation:"]
        for i, turn in enumerate(self.turns, 1):
            parts.append(f"Q{i}: {turn.question}")
            if turn.sql:
                parts.append(f"SQL{i}: {turn.sql}")
            parts.append(f"A{i}: {turn.answer}")
        return "\n".join(parts)
        
    def has_history(self) -> bool:
        return len(self.turns) > 0

    @staticmethod
    def _summarize_rows(rows: list[dict], max_rows: int = 5) -> str:
        if not rows:
            return "No results"
        return str(rows[:max_rows])
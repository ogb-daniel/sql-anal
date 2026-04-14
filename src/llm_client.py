from __future__ import annotations

import json
import os
import time
from typing import Any
from src.observability import  logger

from src.types import SQLGenerationOutput, AnswerGenerationOutput

DEFAULT_MODEL = "openai/gpt-5-nano"


class OpenRouterLLMClient:
    """LLM client using the OpenRouter SDK for chat completions."""

    provider_name = "openrouter"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        try:
            from openrouter import OpenRouter
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install 'openrouter'.") from exc
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        self._client = OpenRouter(api_key=api_key)
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _chat(self, messages: list[dict[str, str]], temperature: float, max_tokens: int, retries: int = 2) -> str:
        for attempt in range(retries + 1):
            try:
                res = self._client.chat.send(
                    messages=messages,
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                )

                # Required for efficiency evaluation - see README.md for details.
                usage = getattr(res, "usage", None)
                if usage:
                    self._stats["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
                    self._stats["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
                    self._stats["total_tokens"] += int(getattr(usage, "total_tokens", 0) or 0)
                self._stats["llm_calls"] += 1


                choices = getattr(res, "choices", None) or []
                if not choices:
                    raise RuntimeError("OpenRouter response contained no choices.")
                content = getattr(getattr(choices[0], "message", None), "content", None)
                if not isinstance(content, str):
                    raise RuntimeError("OpenRouter response content is not text.")
                return content.strip()
            except Exception as exc:
                if attempt < retries and self._is_retryable(exc):
                    time.sleep(2 ** attempt) 
                    continue
                raise

    @staticmethod
    def _is_retryable(exc):
        error_str = str(exc).lower()
        return any(term in error_str for term in ["rate limit", "timeout", "502", "503", "529"])

    @staticmethod
    def _extract_sql(text: str) -> tuple[str | None, bool]:
        maybe_json = text.strip()
        if maybe_json.startswith("{") and maybe_json.endswith("}"):
            try:
                parsed = json.loads(maybe_json)
                answerable = parsed.get("answerable", True)
                sql = parsed.get("sql")
                if not answerable or sql is None:
                    return None, False
                if isinstance(sql, str) and sql.strip():
                    sql = sql.strip().rstrip(";").split(";")[0].strip()
                    return sql, True
                return None, False
            except json.JSONDecodeError:
                pass
        lower = text.lower()
        idx = lower.find("select ")
        if idx >= 0:
            sql = text[idx:].strip().rstrip(";").split(";")[0].strip()
            return sql, True
        return None, False

    def generate_sql(self, question: str, context: dict) -> SQLGenerationOutput:
        columns_str = ", ".join(
        f"{c['name']} ({c['type']})" for c in context.get("columns", [])
        )
        sample_values = context.get("sample_values", {})
        samples_str = "\n".join(
        f"  {col}: {vals}" for col, vals in sample_values.items()
        )
        system_prompt = (
        "You are a SQL assistant for a SQLite database with exactly ONE table.\n\n"
        f"Table: {context.get('table_name', 'gaming_mental_health')}\n"
        f"Columns: {columns_str}\n\n"
        f"Sample values for key columns:\n{samples_str}\n\n"
        "Rules:\n"
        "1. Use ONLY the table and columns listed above.\n"
        "2. When a question involves subjective terms like 'high', 'low', 'younger', 'older', "
        "use reasonable thresholds or comparisons. Only mark as unanswerable if the required "
        "data columns fundamentally do not exist.\n"
        "3. Use exact column names as listed (case-sensitive).\n\n"
        "Respond with ONLY a JSON object in this exact format:\n"
        '{"sql": "<your query>", "answerable": true}\n\n'
        "If the question CANNOT be answered with the available columns:\n"
        '{"sql": null, "answerable": false}\n\n'
        "Output ONLY the JSON object. No explanations, no markdown."
        )
        user_prompt = f"Question: {question}\n\nGenerate a SQL query to answer this question."

        start = time.perf_counter()
        error = None
        sql = None

        try:
            text = self._chat(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.0,
                max_tokens=500,
            )
            sql, answerable = self._extract_sql(text)
            if not answerable:
                error = "Question cannot be answered"
        except Exception as exc:
            error = str(exc)
            logger.error(error)

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model

        return SQLGenerationOutput(
            sql=sql,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    def generate_answer(self, question: str, sql: str | None, rows: list[dict[str, Any]]) -> AnswerGenerationOutput:
        if not sql:
            return AnswerGenerationOutput(
                answer="I cannot answer this with the available table and schema. Please rephrase using known survey fields.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )
        if not rows:
            return AnswerGenerationOutput(
                answer="Query executed, but no rows were returned.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )

        system_prompt = (
            "You are a concise analytics assistant. "
            "Use only the provided SQL results. Do not invent data."
        )
        user_prompt = (
            f"Question:\n{question}\n\nSQL:\n{sql}\n\n"
            f"Rows (JSON):\n{json.dumps(rows[:30], ensure_ascii=True)}\n\n"
            "Write a concise answer in plain English."
        )

        start = time.perf_counter()
        error = None
        answer = ""

        try:
            answer = self._chat(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2,
                max_tokens=220,
            )
        except Exception as exc:
            error = str(exc)
            answer = f"Error generating answer: {error}"

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model

        return AnswerGenerationOutput(
            answer=answer,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    def pop_stats(self) -> dict[str, Any]:
        out = dict(self._stats or {})
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return out


def build_default_llm_client() -> OpenRouterLLMClient:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required.")
    return OpenRouterLLMClient(api_key=api_key)

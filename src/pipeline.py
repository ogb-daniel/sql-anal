from __future__ import annotations
from src.observability import tracer, logger, request_counter, request_duration, token_counter, stage_duration, sql_validation_failures
from src.conversation import ConversationSession

import sqlite3
import time
from pathlib import Path
import re
from src.llm_client import OpenRouterLLMClient, build_default_llm_client
from src.types import (
    SQLValidationOutput,
    SQLExecutionOutput,
    PipelineOutput,
)


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "gaming_mental_health.sqlite"


class SQLValidationError(Exception):
    pass


class SQLValidator:
    FORBIDDEN_KEYWORDS = {"DELETE", "DROP", "UPDATE", "INSERT", "ALTER", "CREATE", 
                          "TRUNCATE", "REPLACE", "MERGE", "GRANT", "REVOKE", "EXEC"}
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def validate(self, sql: str | None) -> SQLValidationOutput:
        start = time.perf_counter()
        
        if sql is None:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="No SQL provided",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        # Consider what validation is needed for this use case
        # Allow only SELECT
        sql_upper = sql.strip().upper()
        first_keyword = sql_upper.split()[0] if sql_upper.split() else ""
        if first_keyword != "SELECT":
            return SQLValidationOutput(
                is_valid=False, validated_sql=None,
                error=f"Only SELECT queries are allowed. Got: {first_keyword}",
                timing_ms=(time.perf_counter() - start) * 1000,
            )
        
        # Check for forbidden keywords
        for keyword in self.FORBIDDEN_KEYWORDS:
            if re.search(rf'\b{keyword}\b', sql_upper):
                return SQLValidationOutput(
                    is_valid=False, validated_sql=None,
                    error=f"Forbidden keyword '{keyword}' found in query",
                    timing_ms=(time.perf_counter() - start) * 1000,
                )

        # use EXPLAIN to verify syntax
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(f"EXPLAIN QUERY PLAN {sql}")
        except sqlite3.Error as e:
            return SQLValidationOutput(
                is_valid=False, validated_sql=None,
                error=f"SQL syntax error: {e}",
                timing_ms=(time.perf_counter() - start) * 1000,
            )
        return SQLValidationOutput(
            is_valid=True,
            validated_sql=sql,
            error=None,
            timing_ms=(time.perf_counter() - start) * 1000,
        )


class SQLiteExecutor:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def run(self, sql: str | None) -> SQLExecutionOutput:
        start = time.perf_counter()
        error = None
        rows = []
        row_count = 0

        if sql is None:
            return SQLExecutionOutput(
                rows=[],
                row_count=0,
                timing_ms=(time.perf_counter() - start) * 1000,
                error=None,
            )

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchmany(100)]
                row_count = len(rows)
        except Exception as exc:
            error = str(exc)
            rows = []
            row_count = 0

        return SQLExecutionOutput(
            rows=rows,
            row_count=row_count,
            timing_ms=(time.perf_counter() - start) * 1000,
            error=error,
        )


class AnalyticsPipeline:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, llm_client: OpenRouterLLMClient | None = None) -> None:
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)
        self.schema_context = self._load_schema()
        self.validator = SQLValidator(self.db_path) 

    def run(self, question: str, request_id: str | None = None) -> PipelineOutput:
        with tracer.start_as_current_span("pipeline.run") as span:
            span.set_attribute("question", question)
            span.set_attribute("request_id", request_id or "")
            request_counter.add(1)
            logger.info("Pipeline started", extra={"request_id": request_id})

            start = time.perf_counter()

            # Stage 1: SQL Generation
            with tracer.start_as_current_span("sql_generation"):
                sql_gen_output = self.llm.generate_sql(question, self.schema_context)
                stage_duration.record(sql_gen_output.timing_ms, {"stage": "sql_generation"})
                sql = sql_gen_output.sql
                logger.info("SQL generated", extra={
                "request_id": request_id, "stage": "sql_generation",
                "sql": sql, "duration_ms": round(sql_gen_output.timing_ms, 2),
                })

            # Stage 2: SQL Validation
            with tracer.start_as_current_span("sql_validation"):
                validation_output = self.validator.validate(sql)
                stage_duration.record(validation_output.timing_ms, {"stage": "sql_validation"})
                if not validation_output.is_valid:
                    sql = None
                    sql_validation_failures.add(1)
                    logger.warning("SQL validation failed", extra={
                    "request_id": request_id, "error": validation_output.error,
                    })

            # Stage 3: SQL Execution
            with tracer.start_as_current_span("sql_execution"):
                execution_output = self.executor.run(sql)
                stage_duration.record(execution_output.timing_ms, {"stage": "sql_execution"})
                rows = execution_output.rows
            
            #one shot retry
            if (not validation_output.is_valid or execution_output.error) and sql_gen_output.sql is not None:
                logger.warning("SQL failed, attempting retry", extra={
                    "request_id": request_id,
                    "error": validation_output.error or execution_output.error,
                })
                
                retry_prompt = (
                    f"{question}\n\n"
                    f"Previous attempt failed.\n"
                    f"Previous SQL: {sql_gen_output.sql}\n"
                    f"Error: {validation_output.error or execution_output.error}\n"
                    f"Generate a corrected query."
                )
                with tracer.start_as_current_span("sql_generation_retry"):
                    sql_gen_retry = self.llm.generate_sql(retry_prompt, self.schema_context)
                    if sql_gen_retry.sql:
                        with tracer.start_as_current_span("sql_validation_retry"):
                            validation_retry = self.validator.validate(sql_gen_retry.sql)
                            if validation_retry.is_valid:
                                with tracer.start_as_current_span("sql_execution_retry"):
                                    execution_retry = self.executor.run(validation_retry.validated_sql)
                                    if not execution_retry.error:
                                        sql = validation_retry.validated_sql
                                        validation_output = validation_retry
                                        execution_output = execution_retry
                                        rows = execution_retry.rows

                                        sql_gen_output.llm_stats["llm_calls"] += sql_gen_retry.llm_stats.get("llm_calls", 0)
                                        sql_gen_output.llm_stats["prompt_tokens"] += sql_gen_retry.llm_stats.get("prompt_tokens", 0)
                                        sql_gen_output.llm_stats["completion_tokens"] += sql_gen_retry.llm_stats.get("completion_tokens", 0)
                                        sql_gen_output.llm_stats["total_tokens"] += sql_gen_retry.llm_stats.get("total_tokens", 0)
                                        sql_gen_output.intermediate_outputs.append({
                                            "retry": True,
                                            "sql": sql_gen_retry.sql,
                                            "llm_stats": sql_gen_retry.llm_stats,
                                        })
                                        logger.info("Retry succeeded", extra={"request_id": request_id, "sql": sql})
                         
            # Stage 4: Answer Generation
            with tracer.start_as_current_span("answer_generation"):
                answer_output = self.llm.generate_answer(question, sql, rows)
                stage_duration.record(answer_output.timing_ms, {"stage": "answer_generation"})
                        
            # Determine status
            status = "success"
            if sql_gen_output.sql is None and sql_gen_output.error:
                status = "unanswerable"
            elif not validation_output.is_valid:
                status = "invalid_sql"
            elif execution_output.error:
                status = "error"
            elif sql is None:
                status = "unanswerable"

            # Build timings aggregate
            timings = {
                "sql_generation_ms": sql_gen_output.timing_ms,
                "sql_validation_ms": validation_output.timing_ms,
                "sql_execution_ms": execution_output.timing_ms,
                "answer_generation_ms": answer_output.timing_ms,
                "total_ms": (time.perf_counter() - start) * 1000,
            }

            # Build total LLM stats
            total_llm_stats = {
                "llm_calls": sql_gen_output.llm_stats.get("llm_calls", 0) + answer_output.llm_stats.get("llm_calls", 0),
                "prompt_tokens": sql_gen_output.llm_stats.get("prompt_tokens", 0) + answer_output.llm_stats.get("prompt_tokens", 0),
                "completion_tokens": sql_gen_output.llm_stats.get("completion_tokens", 0) + answer_output.llm_stats.get("completion_tokens", 0),
                "total_tokens": sql_gen_output.llm_stats.get("total_tokens", 0) + answer_output.llm_stats.get("total_tokens", 0),
                "model": sql_gen_output.llm_stats.get("model", "unknown"),
            }

            total_ms = (time.perf_counter() - start) * 1000
            request_duration.record(total_ms)
            token_counter.add(total_llm_stats.get("total_tokens", 0))
            span.set_attribute("status", status)
            span.set_attribute("total_tokens", total_llm_stats.get("total_tokens", 0))

            logger.info("Pipeline completed", extra={
            "request_id": request_id, "status": status,
            "duration_ms": round(total_ms, 2),
            "tokens": total_llm_stats.get("total_tokens", 0),
            })

            return PipelineOutput(
                status=status,
                question=question,
                request_id=request_id,
                sql_generation=sql_gen_output,
                sql_validation=validation_output,
                sql_execution=execution_output,
                answer_generation=answer_output,
                sql=sql,
                rows=rows,
                answer=answer_output.answer,
                timings=timings,
                total_llm_stats=total_llm_stats,
            )
    def run_with_session(self, question: str, session: ConversationSession,
                     request_id: str | None = None) -> PipelineOutput:
        if session.has_history():
            context_prompt = session.get_context_prompt()
            augmented_question = f"{context_prompt}\n\nFollow-up question: {question}"
        else:
            augmented_question = question
        
        result = self.run(augmented_question, request_id)
        session.add_turn(question, result.sql, result.answer, result.rows)
        result.question = question
        return result

        
    def _load_schema(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('PRAGMA table_info("gaming_mental_health")')
            columns = [
            {"name": row[1], "type": row[2]} 
            for row in cursor.fetchall()
            ]
            cursor.execute('SELECT COUNT(*) FROM gaming_mental_health')
            row_count = cursor.fetchone()[0]
            sample_values = {}
            for col in ["gender"]:
                cursor.execute(f'SELECT DISTINCT "{col}" FROM gaming_mental_health LIMIT 20')
                sample_values[col] = [r[0] for r in cursor.fetchall()]
            return {
            "table_name": "gaming_mental_health",
            "columns": columns,
            "row_count": row_count,
            "sample_values": sample_values,
            }
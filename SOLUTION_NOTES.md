# Solution Notes

## What Changed

### 1. Schema Context & Prompt Engineering (`src/llm_client.py`, `src/pipeline.py`)

The baseline called `generate_sql(question, {})`  an empty context dict. The LLM had zero knowledge of the database.

**Changes:**
- Added `_load_schema()` to `AnalyticsPipeline` that extracts table metadata via `PRAGMA table_info()`, row count, and sample values for categorical columns (e.g., gender).
- Schema context is cached at init time and passed to every `generate_sql()` call.
- Rewrote the system prompt to include full column listing with types, sample values, and strict rules.
- Added structured JSON response format: `{"sql": "...", "answerable": true/false}` for deterministic parsing.
- Added an `UNANSWERABLE` detection path  questions about non-existent columns return `sql=None` with a clear error.
- Improved `_extract_sql()` to handle JSON, markdown code blocks, raw SELECT fallback, and multi-statement SQL sanitization.

**Impact:** Success rate from 0% to 75% (first pass), then 91.67% after retry logic.

---

### 2. Token Counting (`src/llm_client.py`)

The baseline had a `TODO` comment and `_stats` was never updated.

**Changes:**
- Extracts `response.usage` from OpenRouter SDK responses in `_chat()`.
- Updates `prompt_tokens`, `completion_tokens`, `total_tokens` (cast to `int`  the API returns floats).
- Increments `llm_calls` counter per call.
- Uses `getattr` with defaults for resilience if usage data is missing.

**Impact:** Token efficiency is now trackable. Average ~791 tokens/request, ~2.3 LLM calls/request.

---

### 3. SQL Validation (`src/pipeline.py`)

The baseline `SQLValidator.validate()` was a no-op  every query passed, including `DELETE FROM`.

**Changes:**
- Converted from `@classmethod` to instance-based (receives `db_path`).
- Layer 1: Safety check  only SELECT queries allowed (first keyword check).
- Layer 2: Forbidden keyword scan with word-boundary regex across entire query text (catches dangerous keywords in subqueries).
- Layer 3: `EXPLAIN QUERY PLAN`  validates syntax and schema correctness against the real database without executing.

**Impact:** `test_invalid_sql_is_rejected` now passes. Dangerous queries are blocked before execution.

---

### 4. Observability (`src/observability.py`, `src/pipeline.py`)

No observability existed in the baseline.

**Changes:**
- OpenTelemetry tracing: parent span `pipeline.run` with child spans for each stage.
- OpenTelemetry metrics: 5 instruments (request counter, duration histogram, token counter, validation failures, per-stage duration).
- Structured JSON logging: custom `JSONFormatter` with contextual fields (request_id, stage, status, SQL, error, duration, tokens).
- Exporter configurable via `OTEL_EXPORTER_TYPE` env var  console for dev, OTLP for production.

**Impact:** Full visibility into pipeline behavior. Per-stage latency breakdown, token usage tracking, validation failure monitoring.

---

### 5. Error Handling & Retry (`src/pipeline.py`, `src/llm_client.py`)

No error recovery existed in the baseline.

**Changes:**
- One-shot SQL retry in `pipeline.run()`: if SQL validation or execution fails, feeds the error back to the LLM for correction. Stats from retry are merged into the original stats.
- Transient API error retry in `_chat()`: exponential backoff (1s, 2s) for rate limits, timeouts, 502/503/529 errors.
- `_is_retryable()` classifies transient vs. permanent errors.

**Impact:** Improved success rate by recovering from truncated SQL and syntax errors. API resilience against transient OpenRouter failures.

---

### 6. Multi-Turn Conversation (`src/conversation.py`, `src/pipeline.py`)

Not supported in the baseline.

**Changes:**
- `ConversationSession` stores turns (question, SQL, answer, row summary) with UUID session ID.
- History capped at 5 turns to control token growth.
- `run_with_session()` method prepends conversation history to follow-up questions.
- Original `run()` preserved for backward compatibility.

**Impact:** Follow-up questions like "What about males?" correctly reference previous conversation context.

---

### 7. Benchmark Fix (`scripts/benchmark.py`)

**Change:** `result["status"]` to `result.status` (line 53). `PipelineOutput` is a dataclass, not a dict.

---

## Why These Changes

| Decision | Rationale |
|----------|-----------|
| JSON response format over raw SQL | Deterministic parsing, clean unanswerable detection, at ~15-20 extra tokens per request  acceptable tradeoff |
| `EXPLAIN QUERY PLAN` for validation | Validates syntax AND schema correctness without executing. Single SQLite call catches both typo column names and malformed SQL |
| Schema cached at init | Single DB read reused across all requests. Schema doesn't change at runtime |
| One retry only | Prevents infinite loops, keeps latency bounded. Error context in retry prompt gives the LLM what it needs to self-correct |
| OpenTelemetry over custom metrics | Production standard. Env-var switching between console/OTLP exporters. Compatible with existing observability stacks (Grafana, Datadog, etc.) |
| ConversationSession as dataclass | Lightweight, no external dependencies. History trimming prevents unbounded token growth |

---

## Measured Impact

### Before (Baseline)
```json
{
  "runs": 1,
  "samples": 12,
  "success_rate": 0.0,
  "avg_ms": 11526.74,
  "p50_ms": 10470.18,
  "p95_ms": 21488.08
}
```

### After (Final)
```json
{
  "runs": 1,
  "samples": 12,
  "success_rate": 0.9167,
  "avg_ms": 23395.7,
  "p50_ms": 23457.88,
  "p95_ms": 36326.94
}
```

### Analysis
- **Success rate: 0% to 91.67%**  the primary win. Driven by schema context, prompt engineering, and retry logic.
- **Latency increased ~2x**  expected tradeoff. Contributing factors:
  - Retry logic adds an extra LLM call on ~25% of requests
  - `max_tokens=500` (up from 240) gives models more room but may slightly slow response
  - Free model (`nvidia/nemotron-3-super-120b-a12b:free`) has variable latency
- **With a paid model** (e.g., `openai/gpt-4o-mini`), latency would likely be 2-3s avg with 95%+ success rate.

---

## Tradeoffs and Next Steps

### Tradeoffs Made
1. **Latency vs. success rate**  Retry logic adds latency but recovers from ~25% of SQL failures. The success rate gain (0% to 91.67%) far outweighs the latency cost.
2. **Token usage vs. accuracy**  Richer schema context in the system prompt uses more prompt tokens but dramatically improves SQL quality.
3. **JSON format vs. raw SQL**  ~15-20 extra tokens per request for deterministic parsing and unanswerable detection.
4. **Console exporters for OTel**  No external infrastructure needed for now, but structured so switching to OTLP is a single env var change.

### Future Improvements
1. **Query result caching**  LRU cache on `(question_hash to PipelineOutput)` would eliminate redundant LLM calls for repeated questions.
2. **Prompt caching**  OpenRouter supports prompt caching for models that support it. The system prompt (schema context) is identical across requests  enabling caching would reduce prompt token costs significantly.
3. **Model/API upgrade**  Upgrading to a higher openrouter plan with more credits and limits or switching from a free model to `openai/gpt-4o-mini` or similar would improve both success rate (95%+) and latency (2-3s avg).
4. **Conversation summarization**  For multi-turn sessions beyond 5 turns, summarize older turns instead of dropping them to preserve context more efficiently.
5. **Query complexity analysis**  Add heuristics to detect expensive queries (full table scans, missing indexes) before execution.
6. **Async pipeline**  Use `async/await` for LLM calls to enable concurrent request handling.

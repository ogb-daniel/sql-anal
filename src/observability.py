import logging
import json
import time
from contextlib import contextmanager
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

resource = Resource.create({"service.name": "sql-analytics-pipeline"})

# Tracing
tracer_provider = TracerProvider(resource=resource)
# consolespan for dev, switch to OTLPSpanExporter for prod
tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("sql_analytics_pipeline")

# Metrics
metric_reader = PeriodicExportingMetricReader(ConsoleMetricReader(), export_interval_millis=60000)
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("sql_analytics_pipeline")

request_counter = meter.create_counter("pipeline.requests", description="Total pipeline requests")
request_duration = meter.create_histogram("pipeline.duration_ms", description="Pipeline e2e duration")
token_counter = meter.create_counter("pipeline.tokens", description="Total tokens consumed")
sql_validation_failures = meter.create_counter("pipeline.sql_validation_failures")
stage_duration = meter.create_histogram("pipeline.stage_duration_ms", description="Per-stage duration")

# Logging

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

def setup_logging(level=logging.INFO):
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger("sql_analytics")
    root.setLevel(level)
    root.addHandler(handler)
    return root
logger = setup_logging()

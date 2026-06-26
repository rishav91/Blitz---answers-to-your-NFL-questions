"""Dev-time observability (ADR-008) — OTel traces/metrics/logs shipped to the
collector in observability/docker-compose.yml, visualized in Grafana
(Tempo/Prometheus/Loki). Scoped as transparency into what each node is
doing, not production monitoring — see PRD.md §Goals & non-goals.
"""

import functools
import logging
import os
import time

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "blitz-nfl-agent")
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

_initialized = False


def setup_observability() -> None:
    """Wires up OTel tracing/metrics/logging and LangChain auto-instrumentation.
    Idempotent — Streamlit re-runs this module on every interaction."""
    global _initialized
    if _initialized:
        return

    resource = Resource.create({"service.name": SERVICE_NAME})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{OTLP_ENDPOINT}/v1/traces"))
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{OTLP_ENDPOINT}/v1/metrics")
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{OTLP_ENDPOINT}/v1/logs"))
    )
    set_logger_provider(logger_provider)

    # Console handler for the streamlit-run terminal; OTel handler ships the
    # same records to Loki with the active trace/span IDs attached.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.StreamHandler())
    root_logger.addHandler(LoggingHandler(logger_provider=logger_provider))
    LoggingInstrumentor().instrument(set_logging_format=True)

    # Auto-traces every init_chat_model call in graph/llm.py — prompt,
    # completion, tokens, latency — with no changes to that module.
    from openinference.instrumentation.langchain import LangChainInstrumentor

    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

    _initialized = True


def get_tracer():
    return trace.get_tracer(SERVICE_NAME)


_meter = None


def get_meter():
    global _meter
    if _meter is None:
        _meter = metrics.get_meter(SERVICE_NAME)
    return _meter


_node_duration_histogram = None


def _get_node_duration_histogram():
    global _node_duration_histogram
    if _node_duration_histogram is None:
        _node_duration_histogram = get_meter().create_histogram(
            "blitz_node_duration_seconds",
            unit="s",
            description="Wall-clock duration of each LangGraph node call",
        )
    return _node_duration_histogram


_requests_counter = None


def get_requests_counter():
    """Bumped in router_node, labeled by classified intent."""
    global _requests_counter
    if _requests_counter is None:
        _requests_counter = get_meter().create_counter(
            "blitz_requests_total",
            description="Questions classified by router_node, by intent",
        )
    return _requests_counter


_reflection_outcome_counter = None


def get_reflection_outcome_counter():
    """Bumped in reflection_node, labeled by outcome (pass/retry/exhausted)
    and failure_kind (grounding/coverage/none)."""
    global _reflection_outcome_counter
    if _reflection_outcome_counter is None:
        _reflection_outcome_counter = get_meter().create_counter(
            "blitz_reflection_outcome_total",
            description="reflection_node verdicts, by outcome and failure kind",
        )
    return _reflection_outcome_counter


# Span attributes worth pulling out of GraphState if the node set them —
# see graph/state.py for the field/node mapping.
_STATE_ATTRIBUTES = (
    "intent",
    "season",
    "game_type",
    "week",
    "semantic_query",
    "retry_count",
    "last_failure",
    "failure_reason",
)


def _state_attributes(state: dict) -> dict:
    return {
        f"blitz.state.{key}": str(state[key])
        for key in _STATE_ATTRIBUTES
        if state.get(key) is not None
    }


def traced_node(name: str):
    """Decorator for LangGraph node functions: opens span `node.<name>` (so
    nested LLM-call spans from LangChainInstrumentor attach under it),
    records GraphState fields as span attributes, times the call into
    `blitz_node_duration_seconds`, and logs entry/exit."""

    def decorator(fn):
        logger = logging.getLogger(f"blitz.nodes.{name}")

        @functools.wraps(fn)
        def wrapper(state, *args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(f"node.{name}") as span:
                for key, value in _state_attributes(state).items():
                    span.set_attribute(key, value)
                logger.info("%s starting", name, extra=_state_attributes(state))

                start = time.perf_counter()
                try:
                    result = fn(state, *args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    raise
                duration = time.perf_counter() - start

                _get_node_duration_histogram().record(duration, {"node": name})
                merged = {**_state_attributes(state), **_state_attributes(result or {})}
                for key, value in merged.items():
                    span.set_attribute(key, value)
                logger.info("%s finished in %.3fs", name, duration, extra=merged)

                return result

        return wrapper

    return decorator

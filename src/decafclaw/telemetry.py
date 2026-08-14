"""OpenTelemetry integration for native tracing."""

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

# The API/SDK imports below might raise if opentelemetry is not installed.
# We will catch and fail gracefully if they are missing but config enables OTLP.
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    _HAVE_OTLP = True
except ImportError:
    _HAVE_OTLP = False

log = logging.getLogger(__name__)

_is_initialized = False

def init_tracer(config) -> None:
    """Initialize the OpenTelemetry TracerProvider based on configuration.

    If otlp_endpoint is not configured, we do not set a provider, and the OTel API
    falls back to a NoOpTracer natively.
    """
    global _is_initialized
    if _is_initialized:
        return

    otlp_endpoint = config.telemetry.otlp_endpoint
    if otlp_endpoint:
        if not _HAVE_OTLP:
            log.warning("OTLP endpoint configured but opentelemetry-sdk not installed. Tracing disabled.")
            return

        resource = Resource(attributes={
            SERVICE_NAME: config.telemetry.otlp_service_name
        })
        provider = TracerProvider(resource=resource)

        # Use SimpleSpanProcessor if we want immediate export, otherwise Batch is better.
        # But BatchSpanProcessor is standard.
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)
        log.info(f"OpenTelemetry tracing enabled, exporting to {otlp_endpoint}")

    _is_initialized = True

def get_tracer(name: str):
    """Return an OpenTelemetry tracer for the given module name."""
    return trace.get_tracer(name)

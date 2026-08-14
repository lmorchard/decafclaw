
from unittest.mock import AsyncMock, patch

import pytest
from opentelemetry import trace


@pytest.fixture(autouse=True)
def mock_friction_llm(request):
    if "friction" in request.node.name:
        with patch("decafclaw.friction.call_structured", new_callable=AsyncMock) as mock_call_structured:
            mock_call_structured.return_value = {
                "themes": [
                    {"theme": "Use standard logger instead of print", "proposed_addition": "Always use the standard logger, never use print.", "occurrences": 3}
                ]
            }
            yield mock_call_structured
    else:
        yield

@pytest.fixture(autouse=True)
def reset_otlp_singletons():
    yield
    trace._TRACER_PROVIDER = None
    if hasattr(trace._TRACER_PROVIDER_SET_ONCE, "__bool__"):
        # Actually it's an object `_SetOnce` in newer opentelemetry.
        pass

    # In opentelemetry-api 1.x, there is no _TRACER_PROVIDER_SET_ONCE boolean.
    # It might be in opentelemetry.util._once.Once
    if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
        trace._TRACER_PROVIDER_SET_ONCE._done = False

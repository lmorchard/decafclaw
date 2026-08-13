
from unittest.mock import AsyncMock, patch

import pytest


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

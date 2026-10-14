"""Tests for the tabstack skill tool implementations.

Exercises the SDK >= 2.6.1 typed-event handling (automate / research), the
plain-dict extract/generate responses, and the interactive form-fill flow with
its confirmation gate — without real network calls.
"""

import inspect
import types

import httpx
import pytest
from tabstack import APIStatusError
from tabstack.types import GeotargetGeoTarget

from decafclaw.media import ToolResult
from decafclaw.skills.tabstack import tools

# -- Fakes ------------------------------------------------------------------


def _event(kind, **data_fields):
    """Build a fake SSE event: discriminator + typed-ish .data payload."""
    return types.SimpleNamespace(event=kind, data=types.SimpleNamespace(**data_fields))


def _field(label, ref, required, field_type="text"):
    return types.SimpleNamespace(label=label, ref=ref, required=required, field_type=field_type)


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeAgent:
    def __init__(self, automate_events=None, research_events=None):
        self._automate_events = automate_events or []
        self._research_events = research_events or []
        self.input_calls = []  # list of (request_id, kwargs)

    async def automate(self, **kwargs):
        self.last_automate_kwargs = kwargs
        return _AsyncIter(self._automate_events)

    async def research(self, **kwargs):
        return _AsyncIter(self._research_events)

    async def automate_input(self, request_id, **kwargs):
        self.input_calls.append((request_id, kwargs))
        return types.SimpleNamespace(status="accepted")


class _FakeExtract:
    def __init__(self, json_result=None, markdown_content="# hi"):
        self._json_result = json_result
        self._markdown_content = markdown_content
        self.markdown_calls = []  # list of kwargs

    async def json(self, **kwargs):
        return self._json_result

    async def markdown(self, **kwargs):
        self.markdown_calls.append(kwargs)
        return types.SimpleNamespace(content=self._markdown_content)


class _FakeGenerate:
    def __init__(self, json_result):
        self._json_result = json_result

    async def json(self, **kwargs):
        return self._json_result


class _FakeClient:
    def __init__(self, agent=None, extract=None, generate=None):
        self.agent = agent
        self.extract = extract
        self.generate = generate


class _FakeCtx:
    def __init__(self):
        self.published = []

    async def publish(self, event, **kwargs):
        self.published.append((event, kwargs))


@pytest.fixture
def ctx():
    return _FakeCtx()


@pytest.fixture
def use_client(monkeypatch):
    """Install a fake tabstack client as the module-global _client."""

    def _install(client):
        monkeypatch.setattr(tools, "_client", client)
        return client

    return _install


# -- _match_fields ----------------------------------------------------------


def test_match_fields_matches_by_label_case_insensitive():
    fields = [_field("Email", "E1", True), _field("Full Name", "E2", True)]
    matched, missing = tools._match_fields(fields, {"email": "a@b.com", "full name": "Ada"})
    assert missing == []
    assert {"ref": "E1", "value": "a@b.com"} in matched
    assert {"ref": "E2", "value": "Ada"} in matched


def test_match_fields_reports_missing_required_only():
    fields = [
        _field("Email", "E1", True),
        _field("Phone", "E2", True),
        _field("Company", "E3", False),  # optional, absent → not missing
    ]
    matched, missing = tools._match_fields(fields, {"Email": "a@b.com"})
    assert matched == [{"ref": "E1", "value": "a@b.com"}]
    assert missing == ["Phone"]


# -- automate event handling ------------------------------------------------


async def test_automate_extracts_final_answer_and_publishes(ctx, use_client):
    agent = _FakeAgent(
        automate_events=[
            _event("agent:status", message="working"),
            _event("agent:reasoned", reasoning="thinking"),
            _event("task:completed", final_answer="the answer"),
        ]
    )
    use_client(_FakeClient(agent=agent))

    result = await tools.tool_tabstack_automate(ctx, task="do a thing")

    assert result == "the answer"
    # progress published for status + reasoned
    msgs = [k["message"] for e, k in ctx.published if e == "tool_status"]
    assert "working" in msgs and "thinking" in msgs


async def test_automate_no_final_answer_returns_error(ctx, use_client):
    agent = _FakeAgent(automate_events=[_event("agent:status", message="working")])
    use_client(_FakeClient(agent=agent))

    result = await tools.tool_tabstack_automate(ctx, task="do a thing")
    assert "without a final answer" in result


async def test_automate_non_interactive_omits_interactive_kwarg(ctx, use_client):
    agent = _FakeAgent(automate_events=[_event("complete", final_answer="x")])
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t", url="http://x")
    assert "interactive" not in agent.last_automate_kwargs
    assert "data" not in agent.last_automate_kwargs


async def test_automate_non_interactive_drops_data(ctx, use_client):
    """`data` must not reach tabstack unless interactive=true — otherwise personal
    data leaks without the caller opting into the confirmation-gated form-fill."""
    agent = _FakeAgent(automate_events=[_event("complete", final_answer="x")])
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t", data={"Email": "a@b.com"}, interactive=False)
    assert "data" not in agent.last_automate_kwargs
    assert "interactive" not in agent.last_automate_kwargs


async def test_automate_uninitialized_client_returns_error(ctx, monkeypatch):
    """A missing/unactivated skill must return a formatted error like the other
    tabstack tools, not raise out of the tool call."""
    monkeypatch.setattr(tools, "_client", None)

    result = await tools.tool_tabstack_automate(ctx, task="t")
    assert result.startswith("[error:")


# -- guardrails / geo_target forwarding --------------------------------------


async def test_automate_forwards_guardrails(ctx, use_client):
    agent = _FakeAgent(automate_events=[_event("complete", final_answer="x")])
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t", guardrails="do not enter payment details")

    assert agent.last_automate_kwargs["guardrails"] == "do not enter payment details"


async def test_automate_forwards_geo_target(ctx, use_client):
    agent = _FakeAgent(automate_events=[_event("complete", final_answer="x")])
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t", geo_target="US")

    assert agent.last_automate_kwargs["geo_target"] == GeotargetGeoTarget(country="US")


async def test_automate_omits_unset_optional_params(ctx, use_client):
    """Omitted guardrails/geo_target must not reach the SDK — default behavior."""
    agent = _FakeAgent(automate_events=[_event("complete", final_answer="x")])
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t", url="http://x")

    assert "guardrails" not in agent.last_automate_kwargs
    assert "geo_target" not in agent.last_automate_kwargs


async def test_automate_does_not_forward_empty_optional_params(ctx, use_client):
    """None/empty values must not be forwarded as empty SDK options."""
    agent = _FakeAgent(automate_events=[_event("complete", final_answer="x")])
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t", guardrails="", geo_target="")

    assert "guardrails" not in agent.last_automate_kwargs
    assert "geo_target" not in agent.last_automate_kwargs


# -- progress narration -----------------------------------------------------


async def test_automate_progress_surfaces_rich_events(ctx, use_client):
    agent = _FakeAgent(
        automate_events=[
            _event("agent:step", current_iteration=2),
            _event("browser:navigated", title="Signup", url="http://x/s"),
            _event("agent:extracted", extracted_data="price is $5"),
            _event("browser:action_completed", success=True),  # success → no line
            _event("browser:action_completed", success=False, error="element gone"),
            _event("task:validated", completion_quality="excellent"),
            _event("complete", final_answer="done"),
        ]
    )
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t")
    msgs = [k["message"] for e, k in ctx.published if e == "tool_status"]

    assert "step 2" in msgs
    assert "opened Signup — http://x/s" in msgs
    assert "extracted: price is $5" in msgs
    assert "action failed: element gone" in msgs
    assert "validated (excellent)" in msgs
    # the successful action_completed must NOT emit a line
    assert msgs.count("action failed: element gone") == 1


async def test_automate_progress_never_leaks_typed_value(ctx, use_client):
    """agent:action carries the text typed into a field — it must not be echoed."""
    agent = _FakeAgent(
        automate_events=[
            _event("agent:action", action="type", ref="E1", value="ada@secret.com"),
            _event("complete", final_answer="done"),
        ]
    )
    use_client(_FakeClient(agent=agent))

    await tools.tool_tabstack_automate(ctx, task="t")
    msgs = [k["message"] for e, k in ctx.published if e == "tool_status"]

    assert "→ type (E1)" in msgs
    assert not any("ada@secret.com" in m for m in msgs)


# -- error reporting --------------------------------------------------------


async def test_automate_top_level_error_event_surfaced(ctx, use_client):
    agent = _FakeAgent(
        automate_events=[
            _event(
                "error", error=types.SimpleNamespace(message="navigation blocked by policy", code="E", timestamp="t")
            ),
        ]
    )
    use_client(_FakeClient(agent=agent))

    result = await tools.tool_tabstack_automate(ctx, task="t")
    assert "navigation blocked by policy" in result
    assert "without a final answer" not in result


async def test_automate_unsuccessful_complete_reports_structured_error(ctx, use_client):
    agent = _FakeAgent(
        automate_events=[
            _event(
                "complete",
                final_answer=None,
                success=False,
                error=types.SimpleNamespace(message="ran out of iterations", code="MAX_ITERATIONS"),
            ),
        ]
    )
    use_client(_FakeClient(agent=agent))

    result = await tools.tool_tabstack_automate(ctx, task="t")
    assert "ran out of iterations" in result


# -- interactive form-fill --------------------------------------------------


async def test_interactive_approve_submits_matched_fields(ctx, use_client, monkeypatch):
    form = _event(
        "interactive:form_data:request",
        request_id="req-1",
        page_url="http://x/form",
        form_description="Signup",
        fields=[_field("Email", "E1", True)],
    )
    agent = _FakeAgent(automate_events=[form, _event("complete", final_answer="done")])
    use_client(_FakeClient(agent=agent))

    approvals = []

    async def fake_confirm(ctx, **kwargs):
        approvals.append(kwargs)
        return {"approved": True}

    monkeypatch.setattr(tools, "request_confirmation", fake_confirm)

    result = await tools.tool_tabstack_automate(ctx, task="sign up", interactive=True, data={"Email": "a@b.com"})

    assert result == "done"
    assert agent.last_automate_kwargs["interactive"] is True
    # interactive=true → data is forwarded so tabstack can request matching fields
    assert agent.last_automate_kwargs["data"] == {"Email": "a@b.com"}
    # confirmation was shown, then matched fields submitted
    assert approvals and approvals[0]["tool_name"] == "tabstack_automate"
    assert agent.input_calls == [("req-1", {"fields": [{"ref": "E1", "value": "a@b.com"}]})]


async def test_interactive_missing_required_cancels_without_confirmation(ctx, use_client, monkeypatch):
    """Guard sabotage-check: a missing required field must cancel the request and
    must NOT reach the confirmation gate. Removing the missing-field branch in
    _handle_form_request makes this fail."""
    form = _event(
        "interactive:form_data:request",
        request_id="req-2",
        page_url="http://x/form",
        form_description="Signup",
        fields=[_field("Email", "E1", True), _field("Phone", "E2", True)],
    )
    agent = _FakeAgent(automate_events=[form, _event("task:aborted", final_answer="aborted")])
    use_client(_FakeClient(agent=agent))

    confirm_called = False

    async def fake_confirm(ctx, **kwargs):
        nonlocal confirm_called
        confirm_called = True
        return {"approved": True}

    monkeypatch.setattr(tools, "request_confirmation", fake_confirm)

    result = await tools.tool_tabstack_automate(ctx, task="sign up", interactive=True, data={"Email": "a@b.com"})

    assert confirm_called is False
    assert agent.input_calls == [("req-2", {"cancelled": True})]
    assert "Phone" in result and "could not fill" in result


async def test_interactive_denied_cancels_and_reports(ctx, use_client, monkeypatch):
    form = _event(
        "interactive:form_data:request",
        request_id="req-3",
        page_url="http://x/form",
        form_description="Signup",
        fields=[_field("Email", "E1", True)],
    )
    agent = _FakeAgent(automate_events=[form, _event("complete", final_answer="done")])
    use_client(_FakeClient(agent=agent))

    async def fake_confirm(ctx, **kwargs):
        return {"approved": False}

    monkeypatch.setattr(tools, "request_confirmation", fake_confirm)

    result = await tools.tool_tabstack_automate(ctx, task="sign up", interactive=True, data={"Email": "a@b.com"})

    assert agent.input_calls == [("req-3", {"cancelled": True})]
    assert "declined" in result


# -- _safe_input ------------------------------------------------------------


def _status_error(code: int):
    request = httpx.Request("POST", "http://x/input")
    response = httpx.Response(code, request=request)
    return APIStatusError("boom", response=response, body=None)


async def test_safe_input_swallows_410_gone():
    """An expired/consumed input window (410 Gone) is expected — swallow it."""

    class _Agent:
        async def automate_input(self, request_id, **kwargs):
            raise _status_error(410)

    client = types.SimpleNamespace(agent=_Agent())
    # must not raise
    await tools._safe_input(client, "req", cancelled=True)


async def test_safe_input_reraises_non_410():
    """Network/auth/5xx failures leave the run in an undefined state — surface them."""

    class _Agent:
        async def automate_input(self, request_id, **kwargs):
            raise _status_error(500)

    client = types.SimpleNamespace(agent=_Agent())
    with pytest.raises(APIStatusError):
        await tools._safe_input(client, "req", fields=[])


# -- research ---------------------------------------------------------------


async def test_research_extracts_report(ctx, use_client):
    agent = _FakeAgent(
        research_events=[
            _event("planning:start", message="planning"),
            _event("complete", message="done", report="the report"),
        ]
    )
    use_client(_FakeClient(agent=agent))

    result = await tools.tool_tabstack_research(ctx, query="q")
    assert isinstance(result, ToolResult)
    assert result.text == "the report"
    assert result.data["query"] == "q"


async def test_research_no_report_returns_error(ctx, use_client):
    agent = _FakeAgent(research_events=[_event("planning:start", message="planning")])
    use_client(_FakeClient(agent=agent))

    result = await tools.tool_tabstack_research(ctx, query="q")
    assert "without a final answer" in result.text


async def test_research_error_event_surfaced(ctx, use_client):
    agent = _FakeAgent(
        research_events=[
            _event("planning:start", message="planning"),
            _event(
                "error",
                message="research failed: rate limited",
                error=types.SimpleNamespace(message="429", name="RateLimit"),
            ),
        ]
    )
    use_client(_FakeClient(agent=agent))

    result = await tools.tool_tabstack_research(ctx, query="q")
    assert "research failed: rate limited" in result.text
    assert "without a final answer" not in result.text


# -- extract / generate dict shapes -----------------------------------------


async def test_extract_markdown_returns_content(ctx, use_client):
    extract = _FakeExtract()
    use_client(_FakeClient(extract=extract))

    result = await tools.tool_tabstack_extract_markdown(ctx, url="http://x")

    assert isinstance(result, ToolResult)
    assert result.text == "# hi"
    assert result.data["url"] == "http://x"
    assert "geo_target" not in extract.markdown_calls[0]


async def test_extract_markdown_forwards_geo_target(ctx, use_client):
    extract = _FakeExtract()
    use_client(_FakeClient(extract=extract))

    await tools.tool_tabstack_extract_markdown(ctx, url="http://x", geo_target="US")

    assert extract.markdown_calls[0]["geo_target"] == GeotargetGeoTarget(country="US")


async def test_extract_markdown_does_not_forward_empty_geo_target(ctx, use_client):
    extract = _FakeExtract()
    use_client(_FakeClient(extract=extract))

    await tools.tool_tabstack_extract_markdown(ctx, url="http://x", geo_target="")

    assert "geo_target" not in extract.markdown_calls[0]


def test_tool_definitions_match_signatures():
    """TOOL_DEFINITIONS schemas must track the tool signatures — a new parameter
    that is not documented (or a stale schema entry) is a control-surface bug."""
    for name, fn in [
        ("tabstack_automate", tools.tool_tabstack_automate),
        ("tabstack_extract_markdown", tools.tool_tabstack_extract_markdown),
    ]:
        schema = next(t for t in tools.TOOL_DEFINITIONS if t["function"]["name"] == name)
        params = inspect.signature(fn).parameters
        props = set(schema["function"]["parameters"]["properties"])
        assert props == {p for p in params if p != "ctx"}, f"{name}: {props}"
        required = set(schema["function"]["parameters"]["required"])
        assert required == {p for p in params if p != "ctx" and params[p].default is inspect.Parameter.empty}, (
            f"{name}: {required}"
        )


async def test_extract_json_returns_plain_dict(ctx, use_client):
    use_client(_FakeClient(extract=_FakeExtract({"price": 42})))

    result = await tools.tool_tabstack_extract_json(ctx, url="http://x", json_schema={"type": "object"})
    assert result.data["result"] == {"price": 42}
    assert '"price": 42' in result.text


async def test_generate_returns_plain_dict_json(ctx, use_client):
    use_client(_FakeClient(generate=_FakeGenerate({"summary": "hi"})))

    result = await tools.tool_tabstack_generate(
        ctx, url="http://x", json_schema={"type": "object"}, instructions="summarize"
    )
    assert '"summary": "hi"' in result

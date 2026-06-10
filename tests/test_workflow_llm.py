from types import SimpleNamespace

import pytest

from decafclaw.workflow import llm as wf_llm


@pytest.mark.asyncio
async def test_returns_parsed_tool_args(monkeypatch):
    async def fake_call_llm(config, messages, *, tools, model_name):
        return {"tool_calls": [{"function": {"arguments": '{"q": "why?"}'}}]}
    monkeypatch.setattr(wf_llm, "call_llm", fake_call_llm)

    out = await wf_llm.call_structured(
        SimpleNamespace(config=object()),
        system="s", user_msg="u", schema={"type": "object"},
        tool_name="submit", model="m",
    )
    assert out == {"q": "why?"}


@pytest.mark.asyncio
async def test_retries_on_narrate_then_succeeds(monkeypatch):
    calls = []

    async def fake_call_llm(config, messages, *, tools, model_name):
        calls.append(messages)
        if len(calls) == 1:
            return {"content": "I think the answer is...", "tool_calls": None}
        return {"tool_calls": [{"function": {"arguments": '{"ok": true}'}}]}
    monkeypatch.setattr(wf_llm, "call_llm", fake_call_llm)

    out = await wf_llm.call_structured(
        SimpleNamespace(config=object()),
        system="s", user_msg="u", schema={}, tool_name="submit", model="m",
    )
    assert out == {"ok": True}
    assert len(calls) == 2  # one narrate-stall, one nudged retry


@pytest.mark.asyncio
async def test_raises_after_exhausting_retries(monkeypatch):
    async def fake_call_llm(config, messages, *, tools, model_name):
        return {"content": "prose", "tool_calls": None}
    monkeypatch.setattr(wf_llm, "call_llm", fake_call_llm)

    with pytest.raises(RuntimeError):
        await wf_llm.call_structured(
            SimpleNamespace(config=object()),
            system="s", user_msg="u", schema={}, tool_name="submit",
            model="m", retries=1,
        )

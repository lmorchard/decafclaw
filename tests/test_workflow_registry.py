import pytest

from decafclaw.workflow.registry import REGISTRY, get_workflow, workflow


def test_decorator_registers_and_lookup_works():
    @workflow("demo_wf")
    async def demo(h):
        return "ok"

    spec = get_workflow("demo_wf")
    assert spec is not None
    assert spec.name == "demo_wf"
    assert spec.fn is demo


def test_unknown_workflow_returns_none():
    assert get_workflow("does_not_exist_xyz") is None


def test_duplicate_name_raises():
    @workflow("dup_wf")
    async def a(h):
        return 1
    with pytest.raises(ValueError):
        @workflow("dup_wf")
        async def b(h):
            return 2

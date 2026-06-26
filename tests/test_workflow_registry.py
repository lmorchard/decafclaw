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


def test_workflow_decorator_default_requires_skills_empty():
    @workflow("test-default")
    async def f(wf):
        pass

    try:
        spec = get_workflow("test-default")
        assert spec is not None
        assert spec.requires_skills == ()
    finally:
        REGISTRY.pop("test-default", None)


def test_workflow_decorator_accepts_requires_skills():
    @workflow("test-with-skills", requires_skills=("tabstack",))
    async def f(wf):
        pass

    try:
        spec = get_workflow("test-with-skills")
        assert spec is not None
        assert spec.requires_skills == ("tabstack",)
    finally:
        REGISTRY.pop("test-with-skills", None)


def test_workflow_decorator_normalizes_list_to_tuple():
    @workflow("test-list", requires_skills=["a", "b"])
    async def f(wf):
        pass

    try:
        spec = get_workflow("test-list")
        assert spec is not None
        assert spec.requires_skills == ("a", "b")
        assert isinstance(spec.requires_skills, tuple)
    finally:
        REGISTRY.pop("test-list", None)


def test_workflow_decorator_rejects_bare_string_requires_skills():
    """`requires_skills="tabstack"` would silently iterate as characters
    via tuple("tabstack"). Catch the bare-string mistake at decoration
    time with a clear suggestion."""
    import pytest

    with pytest.raises(TypeError, match="requires_skills must be a sequence"):
        @workflow("test-bare-string", requires_skills="tabstack")
        async def f(wf):
            pass
    # The decorator raised BEFORE registering, so the registry stays clean.
    assert "test-bare-string" not in REGISTRY

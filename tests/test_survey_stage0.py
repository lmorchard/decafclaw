"""Tests for Stage 0 signals and page calibration (#1738)."""

from decafclaw.archive import write_archive
from decafclaw.survey.stage0 import format_stage0, run_stage0


class StubRetriever:
    name = "stub"

    def __init__(self, mapping):
        self.mapping = mapping

    async def pages_for(self, text):
        return tuple(self.mapping.get(text, ()))


def _user(text, **extra):
    return {"role": "user", "content": text, "timestamp": "2026-01-01T00:00:00", **extra}


def _assistant(text, **extra):
    return {"role": "assistant", "content": text, "timestamp": "2026-01-01T00:00:01", **extra}


async def test_stage0_reports_offered_recap_and_provenance(config):
    write_archive(
        config,
        "conv-one",
        [
            _user("first"),
            _assistant("Next steps:\n- Commit the change\n- Open a PR"),
            _user("Commit the change"),
            _assistant(
                "done",
                provenance={"vault": [{"page": "agent/pages/A.md"}, {"page": "agent/pages/B.md"}]},
            ),
            _user("thanks"),
        ],
    )
    retriever = StubRetriever(
        {
            "Commit the change": ("agent/pages/A.md", "agent/pages/B.md"),
            "first": ("agent/pages/A.md", "agent/pages/C.md"),
            "done": ("agent/pages/B.md",),
            "thanks": ("agent/pages/D.md", "agent/pages/E.md"),
        }
    )
    result = await run_stage0(config, retriever, default_model="m", top_k=2)
    summary = result.summary
    assert summary["corpus"]["conversations"] == 1
    assert summary["corpus"]["boundaries"] == 2
    assert summary["offered_options"]["offered_turns"] == 1
    assert summary["offered_options"]["recapitulated"] == 1
    assert summary["offered_options"]["kinds"] == {"option": 1}
    assert summary["final_turns"] == {"count": 1, "stop_like": 1, "stop_rate": 1.0}
    assert summary["pages"]["truth_sources"] == {"provenance": 1, "retrieval_proxy": 1}
    assert summary["pages"]["baselines"]["retrieval_of_actual"]["n"] == 2
    assert len(result.details) == 2


async def test_stage0_whole_repeat_is_separate(config):
    write_archive(
        config,
        "conv-repeat",
        [
            _user("first"),
            _assistant("I ran `ls -al ~` for you."),
            _user("ls -al ~"),
            _assistant("done"),
        ],
    )
    retriever = StubRetriever({})
    result = await run_stage0(config, retriever, default_model="m", top_k=2)
    offered = result.summary["offered_options"]
    assert offered["offered_turns"] == 0
    assert offered["whole_message_repeat"] == 1
    assert offered["recapitulated"] == 0


async def test_stage0_page_sample_bounds_calibration(config):
    for index in range(5):
        write_archive(
            config,
            f"conv-{index}",
            [_user("first"), _assistant("reply"), _user("second"), _assistant("reply two")],
        )
    result = await run_stage0(config, StubRetriever({}), default_model="m", top_k=2, page_sample=2)
    assert result.summary["corpus"]["boundaries"] == 5
    assert result.summary["corpus"]["page_calibration_sample"] == 2
    assert len(result.details) == 2


async def test_stage0_format_is_readable(config):
    write_archive(config, "conv", [_user("first"), _assistant("reply"), _user("thanks")])
    result = await run_stage0(config, StubRetriever({}), default_model="m", top_k=2)
    rendered = format_stage0(result.summary)
    assert "Offered-option recapitulation" in rendered
    assert "Vault-direction calibration" in rendered

"""ComposerMode tests: the turn-mode enum and its values.

Split from the former monolithic ``test_context_composer.py`` by concern (#1420).
Mode-gated behavior of individual extensions is covered in the retrieval and
assembly sibling files."""

from decafclaw.context_types import ComposerMode

# -- ComposerMode


class TestComposerMode:
    def test_all_modes_exist(self):
        assert ComposerMode.INTERACTIVE.value == "interactive"
        assert ComposerMode.HEARTBEAT.value == "heartbeat"
        assert ComposerMode.SCHEDULED.value == "scheduled"
        assert ComposerMode.CHILD_AGENT.value == "child_agent"

    def test_mode_count(self):
        assert len(ComposerMode) == 4

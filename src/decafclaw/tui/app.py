from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

if TYPE_CHECKING:
    from decafclaw.config import Config
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

class ConfirmationModal(ModalScreen[tuple]):
    """Modal dialog for confirmation requests."""

    BINDINGS = [
        Binding("y", "approve(True)", "Yes"),
        Binding("n", "approve(False)", "No"),
        Binding("a", "always", "Always"),
    ]

    def __init__(self, confirmation_id: str, message: str, action_type: str, action_data: dict, **kwargs):
        super().__init__(**kwargs)
        self.confirmation_id = confirmation_id
        self.message = message
        self.action_type = action_type
        self.action_data = action_data

    def compose(self) -> ComposeResult:
        command = self.action_data.get("command", self.message)
        yield Vertical(
            Label(f"Confirm ({self.action_type}):"),
            Label(command),
            Label("Approve? [y]es / [n]o / [a]lways"),
            id="confirmation_dialog"
        )

    def action_approve(self, approved: bool) -> None:
        self.dismiss((self.confirmation_id, approved, False, False))

    def action_always(self) -> None:
        self.dismiss((self.confirmation_id, True, True, False))

class MessageLog(Static):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.my_text = ""

    def append_text(self, new_text: str):
        self.my_text += new_text
        self.update(self.my_text)

class DecafClawApp(App):
    """Textual TUI for DecafClaw."""

    CSS = """
    #sidebar {
        width: 30;
        dock: left;
        border-right: solid green;
    }
    #message-log {
        height: 1fr;
        border: solid blue;
    }
    #input-box {
        dock: bottom;
        height: 3;
    }
    #confirmation_dialog {
        padding: 1 2;
        background: $surface;
        border: thick $background 80%;
        width: 60;
        height: auto;
        align: center middle;
    }
    ConfirmationModal {
        align: center middle;
        background: $background 80%;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
    ]

    def __init__(self, manager: ConversationManager, config: Config, event_bus: EventBus, **kwargs):
        super().__init__(**kwargs)
        self.manager = manager
        self.config = config
        self.event_bus = event_bus
        self.active_conv_id = "interactive"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListView(id="sidebar")
            with Vertical():
                yield MessageLog("", id="message-log")
        yield Input(placeholder="Type a message...", id="input-box")
        yield Footer()

    def on_mount(self) -> None:
        self.manager.subscribe(self.active_conv_id, self.on_agent_event)
        self.query_one("#input-box").focus()
        self.update_sidebar()

    def update_sidebar(self):
        sidebar = self.query_one("#sidebar", ListView)
        sidebar.clear()
        sidebar.append(ListItem(Label("interactive"), id="conv_interactive"))
        sidebar.append(ListItem(Label("conv_2"), id="conv_2"))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id:
            conv_id = event.item.id.replace("conv_", "")
            self.switch_conversation(conv_id)

    def switch_conversation(self, conv_id: str) -> None:
        self.active_conv_id = conv_id
        log_widget = self.query_one("#message-log", MessageLog)
        log_widget.my_text = ""
        log_widget.update("")
        self.manager.subscribe(self.active_conv_id, self.on_agent_event)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        input_box = event.input
        text = input_box.value.strip()
        if text:
            input_box.value = ""
            log_widget = self.query_one("#message-log", MessageLog)
            log_widget.append_text(f"you> {text}\n")

            def terminal_context_setup(ctx_arg):
                ctx_arg.channel_name = "interactive"

            asyncio.create_task(
                self.manager.send_message(
                    self.active_conv_id, text,
                    user_id=self.config.agent_user_id,
                    context_setup=terminal_context_setup,
                )
            )

    async def on_agent_event(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type", "")

        if event_type == "chunk":
            text = event.get("text", "")
            log_widget = self.query_one("#message-log", MessageLog)
            log_widget.append_text(text)

        elif event_type == "confirmation_request":
            confirmation_id = event.get("confirmation_id", "")
            message = event.get("message", "")
            action_type = event.get("action_type", "")
            action_data = event.get("action_data", {})

            def check_result(result):
                if result:
                    cid, approved, always, add_pattern = result
                    asyncio.create_task(
                        self.manager.respond_to_confirmation(
                            self.active_conv_id, cid,
                            approved=approved, always=always, add_pattern=add_pattern,
                        )
                    )

            self.push_screen(
                ConfirmationModal(confirmation_id, message, action_type, action_data),
                check_result
            )

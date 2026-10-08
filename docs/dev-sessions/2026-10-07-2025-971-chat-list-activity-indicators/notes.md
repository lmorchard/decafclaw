# Notes: Activity Indicators on Chat List Rows in Sidebar (#971)

- Confirmed with user: "finished" indicator acts as an unread completion indicator for background conversations; past conversations start pre-cleared ("idle"), and selecting/reading a finished conversation clears the badge to idle.
- "waiting" state covers both standard confirmations (tool confirm, shell allow) and widget inputs (`ask_user_multiple_choice`, `ask_user_text`) since both use `ConfirmationAction` and `request_confirmation`/`pending_confirmation` in `ConversationManager`.
- WebSocket gateway broadcasts `conversation_status` events over the global event bus so that all conversations visible in the sidebar update live, even though transcript streaming subscriptions are scoped to the active conversation.

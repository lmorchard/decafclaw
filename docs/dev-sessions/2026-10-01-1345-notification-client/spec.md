# Notification REST contracts

Implement #876 on base `4e3f4d23b60052b5ae366dd834b4296e38c89bc9`.
Expose list, count, and read contracts and retain generated types in the inbox.
Preserve authentication, manual limit validation, optional before, and existing error behavior.
Read actions ignore HTTP statuses and bodies. Network failures remain distinct.
Do not change WebSocket messages or add pagination controls.

Runtime evidence comes from real component tests and Chromium requests to test routes.
Contract mutation tests change used inputs, list/count outputs, display, and navigation fields.
Each mutation must pass before the change and fail at the unchanged inbox after regeneration.

# Dev Session Spec: Issue 954 - Configurable & Extensible Shell Auto-Approval Prompt

## 1. Problem Statement

The aux-LLM shell auto-approval evaluator (`check_shell_approval` in `decafclaw/tools/shell_tools.py`) currently relies on a hard-coded prompt:
```
Only auto-approve low risk read-only or harmless commands (like ls, git status, cat). Do not auto-approve anything that modifies state, installs software, makes network requests, etc.
```

Under this policy, the evaluator declines normal development commands—such as running test runners (`pytest`), linters (`ruff`), and routine git operations (`git commit`, `git push` to non-main branches)—even when collaborating on a repository in the workspace.

## 2. Goals & Non-Goals

### Goals
- Make aux-LLM auto-approval prompt configurable via `config.shell`:
  - `aux_approval_guidance`: Free-form prompt guidance string (or path to a guidance file).
  - `aux_approval_presets`: Named preset mappings for situational approval rules.
  - `active_aux_approval_presets`: List of preset names enabled by default.
- Provide standard built-in presets (e.g. `developer`, `github`).
- Support runtime/conversation-scoped additions via `ToolState` (`ctx.tools.active_aux_approval_presets`, `ctx.tools.disabled_aux_approval_presets`, `ctx.tools.aux_approval_guidance`).
- Provide `shell_guidance` tool for the agent to inspect (`list`), enable/disable situational presets, and add/remove prompt guidelines with user approval (`request_confirmation`), supporting both session-level and persistent (`persistent=True`) changes.
- Build an extensible prompt composer in `shell_tools.py` that formats the base prompt and merges all active guidance.
- Maintain full backward compatibility for setups where no extra guidance or presets are configured.
### Non-Goals
- In-conversation confirmation UI widgets ("Approve & remember exception" button) - deferred to a follow-up phase.
- Modifying security monitor hard blocks (`evaluate_command_llm` BLOCK decisions remain supreme and cannot be overridden by aux-LLM guidance).

## 3. Architecture & Design

### Configuration (`ShellConfig` in `config_types.py`)
```python
@dataclass
class ShellConfig:
    aux_approval_enabled: bool = False
    aux_approval_guidance: str = ""
    aux_approval_presets: dict[str, str] = field(default_factory=dict)
    active_aux_approval_presets: list[str] = field(default_factory=list)
```

### Built-in Presets
Standard presets:
- `developer`: Auto-approves development commands (tests, linters, git commit/push to feature branches) within workspace repos, excluding destructive operations or commands outside workspace.
- `github`: Auto-approves read/inspect and safe management `gh` CLI commands (`gh issue`, `gh pr`, `gh run`), excluding repo deletion or release destruction.

### Runtime State (`ToolState` in `context.py`)
- `active_aux_approval_presets: list[str] = field(default_factory=list)`
- `aux_approval_guidance: list[str] = field(default_factory=list)`

### Prompt Composition (`build_aux_approval_prompt`)
- If no guidance or active presets: use the standard strict prompt.
- If guidance or active presets exist:
  - Base prompt adapts to state that additional approval guidelines apply.
  - Active presets and guidance items are collected, resolved (including file loading if guidance specifies an existing text file), and appended under `Additional Approval Guidelines:`.

## 4. Acceptance Criteria
- Unit tests verify prompt composition with no guidance, inline guidance, file guidance, built-in presets, custom presets, and session-level guidance.
- Unit tests verify `check_shell_approval` correctly passes augmented prompt to aux LLM.
- `make check` / lint / typecheck gates pass cleanly.

#!/usr/bin/env python3
"""Generate docs/code-debt/filing-manifest.json from the per-epic issue files.

Single source of truth is the epic markdown in docs/code-debt/. Edit the
markdown (including subdividing M items into X.Y[letter] slices) and rerun
this script from the repo root; do not hand-edit the JSON.

Each issue carries:
- id (X.Y or X.Ya), gh_title, body, size, priority, area, triage, blocked_by
- impl_model: "local" | "strong" (provisional; see STRONG below)
- parent: the X.Y grouping when the issue is a subdivided slice
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "code-debt"

EPIC_FILES = {
    1: "01-security-path-containment.md",
    2: "02-dead-code-latent-bugs.md",
    3: "03-llm-provider-consolidation.md",
    4: "04-config-resolution.md",
    5: "05-tool-failure-contract.md",
    6: "06-tools-registry-dispatch.md",
    7: "07-conversation-manager.md",
    8: "08-context-classification.md",
    9: "09-skills-cleanup.md",
    10: "10-enums-sweep.md",
    11: "11-http-web-split.md",
    12: "12-frontend-consolidation.md",
    13: "13-frontend-robustness.md",
    14: "14-tests-hygiene.md",
}

EPIC_TITLES = {
    1: "Security & path-containment hardening",
    2: "Dead code & latent-bug cleanup",
    3: "LLM provider consolidation",
    4: "Config resolution refactor",
    5: "Tool failure contract & batch execution",
    6: "Tools registry & dispatch refactor",
    7: "ConversationManager decomposition",
    8: "Context classification & model-resolution consolidation",
    9: "Skills cleanup",
    10: "Stringly-typed to enums sweep",
    11: "HTTP/web layer split",
    12: "Frontend consolidation",
    13: "Frontend robustness & generated-code policy",
    14: "Test suite hygiene",
}

EPIC_AREA = {
    1: "web", 2: "core", 3: "llm", 4: "config", 5: "tools", 6: "tools",
    7: "core", 8: "core", 9: "skills", 10: "core", 11: "web", 12: "web",
    13: "web", 14: "core",
}

P0 = {"1.1", "2.1"}
P1_EPICS = {1, 2, 3, 4, 5, 6, 7, 8}

# Provisional implementation-model suggestion.
# "strong" = subtle invariants, large multi-module context, security-adjacent,
# or clever design. Everything not listed is "local" (mechanical, single-module,
# gate-checked). The test suite is the oracle; local items are fair game for a
# small quantized model that iterates red/green, strong items are not.
STRONG = {
    # Epic 1 — shared path resolver spans the whole http layer; sandbox migration
    "1.2", "1.3",
    # Epic 3 — provider retry/SSE/embed semantics are drift-prone even per-module
    "3.3a", "3.3b", "3.3c", "3.4a", "3.4b", "3.4c",
    "3.5a", "3.5b", "3.5c", "3.7", "3.8",
    # Epic 4 — config introspection, import-cycle, and mutable-state split
    "4.1", "4.4", "4.5",
    # Epic 5 — ToolResult contract and concurrency engine
    "5.1", "5.2", "5.4",
    # Epic 6 — registry derivation, import cycle, mutation executor, approval tiers
    "6.2", "6.3", "6.5", "6.6", "6.7",
    # Epic 7 — conversation lifecycle concurrency invariants
    "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8",
    # Epic 8 — canonical classification source and resolver ladders
    "8.1", "8.3",
    # Epic 9 — schema builder and skill-state helper
    "9.6", "9.7", "9.8",
    # Epic 10 — task_mode crosses the transport boundary
    "10.2",
    # Epic 11 — god-module split, dispatcher, auth, response models
    "11.0", "11.1", "11.2", "11.3", "11.4", "11.5", "11.6", "11.7", "11.8",
    "11.9", "11.11", "11.12", "11.14",
    # Epic 12 — big component splits and endpoint/wiring collapods
    "12.1", "12.2", "12.7", "12.9",
    # Epic 13 — browser + error/queue redesign
    "13.1", "13.2", "13.3",
}

HEADING = re.compile(r"^### (\d+)\.(\d+)([a-z])? — (.+)$")
SIZE = re.compile(r"^\*\*Size:\*\*\s*(.+)$", re.M)
TRIAGE = re.compile(r"^\*\*Triage:\*\*\s*(.+)$", re.M)
BLOCK = re.compile(r"blocked-by\s+((?:#?\d+\.\d+[a-z]?\s*)+)", re.IGNORECASE)


def extract_blocked(text: str) -> list[str]:
    found: set[str] = set()
    for m in BLOCK.finditer(text):
        found.update(re.findall(r"\d+\.\d+[a-z]?", m.group(1)))
    return sorted(found, key=lambda s: (int(s.split(".")[0]), int(s.split(".")[1].rstrip("abcdefghijklmnopqrstuvwxyz")), s))


def order_key(eid: str) -> tuple:
    m = re.match(r"(\d+)\.(\d+)([a-z]?)", eid)
    return (int(m.group(1)), int(m.group(2)), m.group(3) or "")


def main() -> None:
    issues: list[dict] = []
    warnings: list[str] = []

    for epic_num in sorted(EPIC_FILES):
        path = DOCS / EPIC_FILES[epic_num]
        text = path.read_text()
        lines = text.splitlines()

        i = 0
        while i < len(lines):
            m = HEADING.match(lines[i])
            if not m:
                i += 1
                continue
            num, sub, letter, title = int(m.group(1)), int(m.group(2)), m.group(3) or "", m.group(4).strip()
            i += 1
            block_lines: list[str] = []
            while i < len(lines):
                if HEADING.match(lines[i]):
                    break
                block_lines.append(lines[i])
                i += 1

            block = "\n".join(block_lines).strip()
            size_m = SIZE.search(block)
            triage_raw_m = TRIAGE.search(block)
            size = size_m.group(1).strip() if size_m else ""
            triage_raw = triage_raw_m.group(1).strip() if triage_raw_m else ""

            eid = f"{num}.{sub}{letter}"
            parent = f"{num}.{sub}" if letter else None
            blocked = extract_blocked(block)

            if "blocked-by" in triage_raw.lower():
                triage = "blocked"
            elif "needs-input" in triage_raw.lower():
                triage = "needs-input"
            elif "needs-definition" in triage_raw.lower():
                triage = "needs-definition"
            else:
                triage = "ready"

            if blocked and triage != "blocked":
                warnings.append(f"{eid}: deps cite {blocked} but triage is {triage!r}")

            priority = ("P0" if eid in P0 else "P1") if epic_num in P1_EPICS else "P2"

            issues.append({
                "id": eid,
                "parent": parent,
                "epic": epic_num,
                "epic_title": EPIC_TITLES[epic_num],
                "gh_title": f"[{eid}] {title}",
                "title": title,
                "size": size,
                "priority": priority,
                "area": EPIC_AREA[epic_num],
                "impl_model": "strong" if eid in STRONG else "local",
                "triage": triage,
                "blocked_by": blocked,
                "body": block,
            })

    issues.sort(key=lambda x: order_key(x["id"]))

    manifest = {
        "_note": (
            "Generated from docs/code-debt/ per-epic markdown; edit the markdown "
            "and regenerate — do not hand-edit this JSON. Priority, area, and "
            "impl_model are provisional suggestions."
        ),
        "repo": "lmorchard/decafclaw",
        "board": {"owner": "lmorchard", "project": 6},
        "labels": {
            "triage_ready": "triage:ready",
            "triage_blocked": "triage:blocked",
            "area_prefix": "area:",
            "area_optional": True,
        },
        "impl_model_meaning": {
            "local": ("mechanical, single-module-ish, gate-checked; suitable for a "
                      "small quantized model iterating red/green against the test suite"),
            "strong": ("subtle invariants, large/high-context, or security-adjacent; "
                       "use a frontier or larger non-quantized model"),
        },
        "issues": issues,
        "ordering_note": (
            "Issues are dependency-safe in sort order (epic, item, slice-letter): "
            "a blocked item's prerequisite always precedes it. File in this order "
            "and resolve blocked_by to the real issue number as you go."
        ),
    }

    out = DOCS / "filing-manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    ready = sum(1 for x in issues if x["triage"] == "ready")
    blocked = sum(1 for x in issues if x["triage"] == "blocked")
    local = sum(1 for x in issues if x["impl_model"] == "local")
    strong = sum(1 for x in issues if x["impl_model"] == "strong")
    print(f"wrote {out}  ({len(issues)} issues: {ready} ready, {blocked} blocked; "
          f"{local} local, {strong} strong)")
    for w in warnings:
        print("WARN", w)


if __name__ == "__main__":
    main()
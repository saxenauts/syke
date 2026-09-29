"""Bounded, read-only orientation over Syke's existing self evidence."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke import __version__
from syke.control import get_receipt, list_receipts, receipt_path
from syke.db import SykeDB
from syke.memory.memex_budget import (
    MEMORY_TOKEN_LIMIT,
    count_memory_tokens,
    measure_memex,
    strip_memex_header,
)
from syke.observe.catalog import active_sources, discovered_roots
from syke.runtime.pi_sessions import find_session_by_id, find_session_by_name, list_sessions

WORKSPACE_SCAN_ENTRY_LIMIT = 10_000
LARGE_WORKSPACE_FILE_BYTES = 10 * 1024 * 1024
WORKSPACE_SOFT_TARGET_BYTES = 3 * 1024 * 1024 * 1024
RUNTIME_SOFT_TARGET_BYTES = 3 * 1024 * 1024 * 1024
CYCLE_RUNTIME_SOFT_TARGET_BYTES = 3 * 1024 * 1024 * 1024
SELF_VIEW_TOKEN_TARGET = 2_500
CHARS_PER_TOKEN = 4
_SELF_VIEW_USAGE_MARKER = "__SELF_VIEW_USAGE__"


def _short(value: object, limit: int = 240) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _path_text(value: str | os.PathLike[str]) -> str:
    raw = os.fspath(value)
    if raw == ":memory:":
        return raw
    return str(Path(raw).expanduser().resolve())


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _sessions_path(
    workspace_root: Path,
    session_dir: Path | None,
) -> Path:
    if session_dir is not None:
        return session_dir.expanduser().resolve()
    return workspace_root.expanduser().resolve().parent / "control" / "sessions"


def _latest_operation(
    latest_receipt: dict[str, Any] | None,
    latest_session: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]] | None:
    if latest_receipt is None:
        return ("session", latest_session) if latest_session else None
    if latest_session is None:
        return ("receipt", latest_receipt)
    if latest_session.get("kind") == "synthesis" and latest_session.get(
        "operation_id"
    ) == latest_receipt.get("id"):
        return "session", latest_session

    receipt_time = _parse_time(
        latest_receipt.get("completed_at") or latest_receipt.get("started_at")
    )
    session_time = _parse_time(
        latest_session.get("completed_at") or latest_session.get("started_at")
    )
    if receipt_time is not None and (session_time is None or receipt_time > session_time):
        return "receipt", latest_receipt
    return "session", latest_session


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _graph_condition(db: SykeDB, user_id: str) -> dict[str, int]:
    graph = db.get_graph_stats(user_id)
    current_memex = db.conn.execute(
        "SELECT COUNT(*) FROM current_memex WHERE singleton = 1 AND user_id = ?",
        (user_id,),
    ).fetchone()[0]
    missing_from_search = db.conn.execute(
        """SELECT COUNT(*)
           FROM (
               SELECT id
               FROM memories
               WHERE user_id = ?
               EXCEPT
               SELECT memory_id FROM memories_fts
           )""",
        (user_id,),
    ).fetchone()[0]
    page_size = int(db.conn.execute("PRAGMA page_size").fetchone()[0] or 0)
    page_count = int(db.conn.execute("PRAGMA page_count").fetchone()[0] or 0)
    free_pages = int(db.conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)

    return {
        "current_memories": int(graph["memories"] or 0),
        "current_links": int(graph["links"] or 0),
        "unlinked_current_memories": int(graph["unlinked"] or 0),
        "current_memex": int(current_memex or 0),
        "current_missing_from_search": int(missing_from_search or 0),
        "sqlite_bytes": page_size * page_count,
        "sqlite_reusable_bytes": page_size * free_pages,
        "sqlite_reusable_pct": round(free_pages / page_count * 100) if page_count else 0,
    }


def _memories_over_budget(db: SykeDB, user_id: str) -> list[tuple[str, int]]:
    """Return current memories above the per-memory budget, largest first."""
    sizes = [
        (str(row["id"]), count_memory_tokens(str(row["content"] or "")))
        for row in db.conn.execute(
            "SELECT id, content FROM memories WHERE user_id = ?",
            (user_id,),
        )
    ]
    return sorted(
        (item for item in sizes if item[1] > MEMORY_TOKEN_LIMIT),
        key=lambda item: item[1],
        reverse=True,
    )


def _workspace_pressure(workspace: Path, graph_path: str) -> dict[str, Any]:
    excluded: set[str] = set()
    if graph_path != ":memory:":
        excluded = {
            graph_path,
            f"{graph_path}-journal",
            f"{graph_path}-shm",
            f"{graph_path}-wal",
        }

    entries_scanned = 0
    file_count = 0
    logical_bytes = 0
    errors = 0
    truncated = False
    large_files: list[tuple[int, str]] = []
    pending = [workspace]

    while pending and not truncated:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entries_scanned >= WORKSPACE_SCAN_ENTRY_LIMIT:
                        truncated = True
                        break
                    entries_scanned += 1
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        absolute_path = os.path.abspath(entry.path)
                        if absolute_path in excluded:
                            continue
                        size = int(entry.stat(follow_symlinks=False).st_size)
                    except OSError:
                        errors += 1
                        continue

                    file_count += 1
                    logical_bytes += size
                    if size >= LARGE_WORKSPACE_FILE_BYTES:
                        relative = str(Path(entry.path).relative_to(workspace))
                        large_files.append((size, relative))
        except OSError:
            errors += 1

    large_files.sort(key=lambda item: (-item[0], item[1]))
    return {
        "entries_scanned": entries_scanned,
        "file_count": file_count,
        "logical_bytes": logical_bytes,
        "errors": errors,
        "truncated": truncated,
        "large_files": large_files[:3],
    }


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count:,} {noun}"


def _source_inventory_lines(
    workspace: Path,
    *,
    home: Path | None,
    selected_sources: tuple[str, ...] | None,
) -> list[str]:
    selected = set(selected_sources) if selected_sources is not None else None
    lines: list[str] = []
    for spec in active_sources():
        if selected is not None and spec.source not in selected:
            continue
        adapter = workspace / "adapters" / f"{spec.source}.md"
        adapter_state = (
            "readable" if adapter.is_file() and os.access(adapter, os.R_OK) else "unavailable"
        )
        roots = discovered_roots(spec, home=home)
        root_parts: list[str] = []
        for root in roots:
            path = Path(root).expanduser().resolve()
            state = "readable" if path.exists() and os.access(path, os.R_OK) else "unavailable"
            root_parts.append(f"{path} ({state})")
        roots_text = "; ".join(root_parts) if root_parts else "none discovered"
        lines.append(
            f"- {spec.source}: adapter {adapter.resolve()} ({adapter_state}); roots: {roots_text}."
        )
    return lines or ["- No external sources are selected for this operation."]


def build_self_view(
    db: SykeDB,
    user_id: str,
    *,
    workspace_root: Path,
    session_dir: Path | None = None,
    cycle_runtime: Path | None = None,
    as_of: str = "unavailable",
    home: Path | None = None,
    selected_sources: tuple[str, ...] | None = None,
) -> str:
    """Render the current self-observation projection without storing it."""
    workspace = workspace_root.expanduser().resolve()
    graph_path = _path_text(db.db_path)
    sessions_path = _sessions_path(workspace, session_dir)
    control_dir = sessions_path.parent
    runtime_root = control_dir / "runtime"
    from syke.runtime.sandbox import sandbox_enabled, sandbox_read_paths

    if sandbox_enabled():
        computer_home = Path.home().expanduser().resolve()
        read_roots = sandbox_read_paths()
        if read_roots == (str(computer_home),):
            read_scope = f"{_short(computer_home, 500)} ($HOME)"
        else:
            read_scope = "; ".join(_short(root, 500) for root in read_roots) or "no computer roots"
        filesystem_boundary = (
            f"- Computer files: {read_scope} readable; only {_short(workspace, 500)} and "
            f"{_short(runtime_root, 500)} writable."
        )
    else:
        filesystem_boundary = (
            "- Computer files: the OS sandbox is unavailable or disabled; model tools have the "
            "permissions of the running process. Treat paths outside "
            f"{_short(workspace, 500)} and {_short(runtime_root, 500)} as read-only by policy."
        )
    memex = db.get_memex(user_id)
    completed_receipts = list_receipts(control_dir, status="completed", limit=1)
    accepted = completed_receipts[0] if completed_receipts else None
    all_receipts = list_receipts(control_dir, limit=1)
    latest_receipt = all_receipts[0] if all_receipts else None
    recent_sessions = list_sessions(sessions_path, limit=20)
    latest_session = recent_sessions[0] if recent_sessions else None
    latest = _latest_operation(latest_receipt, latest_session)

    runtime = "unavailable"
    if latest_session:
        runtime_parts = [
            _short(value, 80)
            for value in (latest_session.get("provider"), latest_session.get("model"))
            if value
        ]
        if runtime_parts:
            runtime = "Pi / " + " / ".join(runtime_parts)

    from syke.config import DAEMON_INTERVAL

    installed_core = Path(__file__).resolve().parents[1]
    prompt_path = Path(__file__).with_name("syke_self.md").resolve()
    composer_path = Path(__file__).with_name("prompt_context.py").resolve()
    tool_contract_path = Path(__file__).with_name("pi_tools.mjs").resolve()
    memex_path = workspace / "MEMEX.md"

    lines = [
        "# Self-observation",
        "",
        f"- As of: {_short(as_of, 160)}.",
        "",
        "## How Syke is running now",
        "",
        "This observation was generated by the host before this invocation. It reports facts "
        "the host can currently establish about Syke; it is not the complete state of the "
        "person, computer, or outside world.",
        "",
        f"- Person served: {_short(user_id, 120)}.",
        f"- Syke installation: version {_short(__version__, 80)} at "
        f"{_short(installed_core, 500)}; "
        "source commit unavailable from the installed package.",
        f"- Runtime: {runtime}.",
        f"- Schedule: configured wake interval {DAEMON_INTERVAL:,} seconds "
        f"({DAEMON_INTERVAL // 60:,} minutes).",
        f"- Effective prompt surfaces: installed system prompt {_short(prompt_path, 500)}; this "
        "host-generated self-observation; accepted MEMEX below; your operating notes below; "
        "current operation below; native tool and host acceptance contracts.",
        "- Pi adds the host date and working directory to its system layer. The operation's "
        "authoritative reference time overrides that generic date for relative-time reasoning. "
        "Project context files and general Pi skill discovery stay disabled; Syke explicitly "
        "loads its private self-learn skill.",
        "",
        "## Durable and protected surfaces",
        "",
        f"- Core: Syke {_short(__version__, 80)}.",
        f"- Installed core (read-only): {_short(installed_core, 500)}.",
        f"- Mutable graph: {_short(graph_path, 500)}.",
        f"- Owned workspace: {_short(workspace, 500)}.",
        f"- Durable operational runtime: {_short(runtime_root, 500)}.",
        f"- Routed MEMEX projection: {_short(memex_path, 500)} "
        f"({'available' if memex_path.is_file() else 'unavailable'}).",
        f"- Protected host receipts: {_short(control_dir / 'receipts', 500)}.",
        f"- Protected incoming records: {_short(control_dir / 'records', 500)}.",
        f"- Protected native Pi sessions: {_short(sessions_path, 500)}.",
        f"- Protected recovery state: {_short(control_dir / 'recovery', 500)}.",
        f"- Installed self-model: {_short(prompt_path, 500)}.",
        f"- Installed prompt composer: {_short(composer_path, 500)}.",
        f"- Self-view size: about {_SELF_VIEW_USAGE_MARKER} / "
        f"{SELF_VIEW_TOKEN_TARGET:,}-token soft target.",
    ]
    current_cycle_runtime = (
        cycle_runtime.expanduser().resolve() if cycle_runtime is not None else None
    )
    if current_cycle_runtime is not None:
        lines.append(f"- Current attempt runtime directory: {_short(current_cycle_runtime, 500)}.")
    if memex:
        memex_time = memex.get("updated_at") or memex.get("created_at") or "unknown"
        lines.append(
            f"- Current MEMEX: row {_short(memex.get('id') or 'unknown', 120)}, "
            f"recorded at {_short(memex_time, 100)}."
        )
    else:
        lines.append("- Current MEMEX: none recorded.")

    lines.extend(
        [
            "",
            "## What you can inspect and change",
            "",
            "The active model tools are `read`, `bash`, `edit`, and `write`. They run from "
            "Syke's owned workspace. Do not assume another tool or path is available.",
            filesystem_boundary,
            "",
            "Computer files outside the declared writable paths are read-only external evidence. "
            "Use the current attempt runtime directory for operational files; earlier "
            "attempts' runtime can be read and copied from. Tools and notes in your workspace "
            "are yours to keep and reuse. Installed code and protected evidence are read-only. "
            "Outbound network access does not make an external claim authoritative.",
            f"- Native tool contracts: {_short(tool_contract_path, 500)}.",
            "",
            "Selected sources, adapter guides, and known native evidence routes:",
            *_source_inventory_lines(
                workspace,
                home=home,
                selected_sources=selected_sources,
            ),
            "",
            "Source selection controls ingestion and orientation, not filesystem permission. An "
            "adapter explains how to inspect a source; it is not evidence from that source. The "
            "native records at the named roots remain authoritative for what they recorded.",
            "",
            "## Graph and search contract",
            "",
            f"The accepted graph is the SQLite database at {graph_path}. Reads and writes use "
            "SQLite through `bash`; there is no separate graph mutation tool.",
            "",
            "Operative tables:",
            "- `memories(id TEXT PRIMARY KEY, user_id TEXT, content TEXT, created_at TEXT, "
            "updated_at TEXT)`",
            "- `links(id TEXT PRIMARY KEY, user_id TEXT, source_id TEXT, target_id TEXT, reason "
            "TEXT, created_at TEXT)`",
            "- `current_memex(singleton INTEGER PRIMARY KEY, id TEXT, user_id TEXT, content TEXT, "
            "created_at TEXT, updated_at TEXT)`",
            "- `memories_fts(memory_id, content)`, an FTS5 index maintained from memory content.",
            "",
            f"The graph is bound to {user_id}; that identity is immutable. Every row in "
            "`memories` is current. Preserve a memory's exact ID and `created_at` when revising "
            "it. Create a row only for a separate durable strand. Delete a memory only when it "
            "no longer belongs in the current graph, and delete its links first.",
            "",
            f"Each memory has a {MEMORY_TOKEN_LIMIT:,}-token budget (o200k_base). The host "
            "rejects a cycle that leaves a created or revised memory over it; a memory already "
            "over at cycle start may shrink or hold, never grow. Arranging within budget is "
            "your judgment: compress, split a subject across linked memories, or move long "
            "chronology to an owned workspace file with a route to it.",
            "",
            "When source relationships matter to the current understanding, explain them "
            "naturally in the memory content and include exact native session or conversation "
            "IDs there when useful and available. A source ID is never required for acceptance; "
            "do not invent one or force this language into a fixed format. The host does not "
            "parse or judge that prose.",
            "",
            "A current link needs a unique ID, a natural-language reason, and two complete IDs "
            "for current memories. Resolve shortened IDs before mutation rather than guessing.",
            "",
            "Revise only the singleton `current_memex` row named here and preserve its identity. "
            "It is separate from ordinary memories. The host records accepted MEMEX versions "
            "outside `syke.db`; do not create history rows in the graph database.",
            "",
            "Use `memories` for exact-ID and chronological inspection. Run "
            "full-text `MATCH` against `memories_fts`, then join "
            "`memories_fts.memory_id = memories.id` for person filters. "
            "`memories.content MATCH ...` is not a valid full-text route.",
            "",
            "The host captures the accepted graph before invocation and afterward checks "
            "database integrity, identity, current MEMEX identity, link "
            "endpoints, memory budgets, and search-index agreement. "
            "A model message is not proof of acceptance. "
            "If the database contradicts this contract, stop graph mutation and preserve the "
            "exact mismatch rather than inventing a schema.",
        ]
    )

    lines.extend(["", "## Accepted continuation"])
    accepted_session: dict[str, Any] | None = None
    if accepted:
        accepted_id = str(accepted.get("id") or "unknown")
        lines.append(
            f"- Cycle {_short(accepted_id, 120)} completed at "
            f"{_short(accepted.get('completed_at') or 'unknown', 100)}."
        )
        lines.append(
            "- MEMEX update recorded by the host: "
            f"{'yes' if accepted.get('memex_updated') else 'no'}."
        )
        accepted_gate = accepted.get("acceptance")
        if isinstance(accepted_gate, dict):
            lines.append(
                "- Acceptance: attempt "
                f"{int(accepted_gate.get('accepted_attempt') or 1)} accepted after "
                f"{int(accepted_gate.get('repair_prompts') or 0)} same-session repair "
                "prompts."
            )
        session_id = accepted.get("session_id")
        accepted_session = (
            find_session_by_id(sessions_path, str(session_id))
            if isinstance(session_id, str) and session_id
            else None
        )
        if accepted_session is None:
            accepted_session = find_session_by_name(
                sessions_path,
                f"syke:synthesis:{accepted_id}",
            )
        if accepted_session:
            lines.append(
                f"- Native session {_short(accepted_session.get('id') or 'unknown', 120)} "
                "is linked to this receipt."
            )
        else:
            lines.append(
                f"- Native session named syke:synthesis:{_short(accepted_id, 120)} was not found."
            )
    else:
        lines.append("- No accepted synthesis receipt is recorded.")

    lines.extend(["", "Accepted continuation details"])
    if accepted:
        lines.append(
            "- Ordinary graph changes are not copied into the receipt. Use the linked native "
            "session to investigate why the current graph changed."
        )
        if accepted_session:
            duration_seconds = float(accepted_session.get("duration_ms") or 0) / 1000
            lines.append(
                f"- Last-cycle use from the native session: {duration_seconds:.1f}s, "
                f"{int(accepted_session.get('input_tokens') or 0):,} input, "
                f"{int(accepted_session.get('output_tokens') or 0):,} output, "
                f"{int(accepted_session.get('cache_read_tokens') or 0):,} cache-read tokens, "
                f"${float(accepted_session.get('cost_usd') or 0):.4f}."
            )
    else:
        lines.append("- No accepted receipt exists.")

    if latest_receipt and (not accepted or latest_receipt.get("id") != accepted.get("id")):
        lines.extend(["", "Latest attempt effects"])
        if latest_receipt.get("status") != "completed":
            failure_reason = latest_receipt.get("error")
            lines.append(
                "- Host-recorded failure or rejection reason: "
                f"{_short(failure_reason or 'unavailable', 500)}."
            )
            acceptance_trace = latest_receipt.get("acceptance")
            if isinstance(acceptance_trace, dict):
                lines.append(
                    "- Acceptance trace: "
                    f"{int(acceptance_trace.get('repair_prompts') or 0)} repair prompts."
                )
                lines.append(
                    "- Exact rejection issues and mechanical facts: "
                    f"{receipt_path(control_dir, str(latest_receipt.get('id') or 'unknown'))}."
                )

    graph = _graph_condition(db, user_id)
    over_budget = _memories_over_budget(db, user_id)
    if over_budget:
        shown = ", ".join(f"`{memory_id}` {tokens:,}" for memory_id, tokens in over_budget[:6])
        more = f", and {len(over_budget) - 6} more" if len(over_budget) > 6 else ""
        memory_budget_line = (
            f"- Memory budget: {len(over_budget)} of {graph['current_memories']:,} current "
            f"memories {'exceeds' if len(over_budget) == 1 else 'exceed'} "
            f"{MEMORY_TOKEN_LIMIT:,} tokens: {shown}{more}."
        )
    else:
        memory_budget_line = ""
    current_memories = _plural(
        graph["current_memories"],
        "current memory",
        "current memories",
    )
    unlinked_memories = _plural(
        graph["unlinked_current_memories"],
        "current memory",
        "current memories",
    )
    missing_search = _plural(
        graph["current_missing_from_search"], "current memory", "current memories"
    )
    lines.extend(
        [
            "",
            "Current graph condition",
            f"- {current_memories}, {_plural(graph['current_links'], 'current link')}.",
            f"- {unlinked_memories} "
            f"{'has' if graph['unlinked_current_memories'] == 1 else 'have'} no current links; "
            "this is graph shape, not an error.",
            f"- Structural signals: {_plural(graph['current_memex'], 'current MEMEX row')}; "
            f"{missing_search} "
            f"{'is' if graph['current_missing_from_search'] == 1 else 'are'} missing from search"
            + (
                "."
                if over_budget
                else f"; none exceed the {MEMORY_TOKEN_LIMIT:,}-token memory budget."
            ),
            *([memory_budget_line] if memory_budget_line else []),
            f"- SQLite pages: {_format_bytes(graph['sqlite_bytes'])} logical, "
            f"{_format_bytes(graph['sqlite_reusable_bytes'])} reusable "
            f"({graph['sqlite_reusable_pct']}%).",
            "- These are cheap structural facts, not a semantic health verdict or a full "
            "SQLite integrity check.",
        ]
    )

    memex_content = str(memex.get("content") or "") if memex else ""
    memex_measurement = measure_memex(memex_content)
    projection_status = "unavailable"
    if memex_path.is_file():
        try:
            projected = memex_path.read_text(encoding="utf-8").strip()
            projected = strip_memex_header(projected)
            projection_status = (
                "available and agrees with canonical content"
                if projected.strip() == memex_content.strip()
                else "available but does not agree with canonical content"
            )
        except OSError:
            projection_status = "unavailable because it could not be read"
    lines.extend(
        [
            "",
            "MEMEX and prompt pressure",
            f"- Current MEMEX: {memex_measurement['tokens']:,} exact "
            f"{memex_measurement['encoding']} tokens / "
            f"{memex_measurement['limit']:,}-token budget "
            f"({memex_measurement['fill_pct']}%); row "
            f"{_short(memex.get('id') if memex else 'none', 120)}.",
            f"- Routed projection: {projection_status} at {_short(memex_path, 500)}.",
            f"- Self-observation: about {_SELF_VIEW_USAGE_MARKER} / "
            f"{SELF_VIEW_TOKEN_TARGET:,}-token soft target.",
        ]
    )

    workspace_state = _workspace_pressure(workspace, graph_path)
    workspace_fill_pct = round(workspace_state["logical_bytes"] / WORKSPACE_SOFT_TARGET_BYTES * 100)
    scan_status = (
        f"partial at the {WORKSPACE_SCAN_ENTRY_LIMIT:,}-entry bound"
        if workspace_state["truncated"]
        else f"complete after {workspace_state['entries_scanned']:,} entries"
    )
    lines.extend(
        [
            "",
            "Current workspace pressure",
            f"- {_plural(workspace_state['file_count'], 'regular file')}, "
            f"{_format_bytes(workspace_state['logical_bytes'])} logical / "
            f"{_format_bytes(WORKSPACE_SOFT_TARGET_BYTES)} soft target "
            f"({workspace_fill_pct}%); scan {scan_status}.",
        ]
    )
    workspace_overage = workspace_state["logical_bytes"] - WORKSPACE_SOFT_TARGET_BYTES
    if workspace_overage > 0:
        overage_prefix = "at least " if workspace_state["truncated"] else ""
        lines.extend(
            [
                f"- Workspace soft target exceeded by {overage_prefix}"
                f"{_format_bytes(workspace_overage)}.",
                "- This is an active maintenance obligation and remains visible in "
                "every fresh cycle until the workspace is below the soft target.",
                "- Inspect what Syke owns and use your judgment to remove, consolidate, "
                "or retain useful artifacts; nothing is deleted automatically.",
            ]
        )
    if workspace_state["large_files"]:
        large_file_text = "; ".join(
            f"{_short(path, 160)} ({_format_bytes(size)})"
            for size, path in workspace_state["large_files"]
        )
        lines.append(f"- Largest files above 10 MiB: {large_file_text}.")
    if workspace_state["errors"]:
        lines.append(
            f"- {_plural(workspace_state['errors'], 'filesystem entry')} could not be read."
        )
    lines.extend(
        [
            "- Graph database files are excluded; symlinks are not followed.",
        ]
    )

    runtime_state = _workspace_pressure(runtime_root, ":memory:")
    runtime_fill_pct = round(runtime_state["logical_bytes"] / RUNTIME_SOFT_TARGET_BYTES * 100)
    runtime_scan_status = (
        f"partial at the {WORKSPACE_SCAN_ENTRY_LIMIT:,}-entry bound"
        if runtime_state["truncated"]
        else f"complete after {runtime_state['entries_scanned']:,} entries"
    )
    lines.extend(
        [
            "",
            "Durable runtime pressure",
            f"- {_plural(runtime_state['file_count'], 'regular file')}, "
            f"{_format_bytes(runtime_state['logical_bytes'])} logical / "
            f"{_format_bytes(RUNTIME_SOFT_TARGET_BYTES)} soft target "
            f"({runtime_fill_pct}%); scan {runtime_scan_status}.",
            "- Runtime is operational continuity state, not part of the owned workspace. The "
            "controller does not automatically delete or promote its contents.",
        ]
    )
    runtime_overage = runtime_state["logical_bytes"] - RUNTIME_SOFT_TARGET_BYTES
    if runtime_overage > 0:
        overage_prefix = "at least " if runtime_state["truncated"] else ""
        lines.append(
            f"- Runtime soft target exceeded by {overage_prefix}{_format_bytes(runtime_overage)}."
        )
    if runtime_state["errors"]:
        lines.append(f"- {_plural(runtime_state['errors'], 'runtime entry')} could not be read.")

    if current_cycle_runtime is not None:
        cycle_state = _workspace_pressure(current_cycle_runtime, ":memory:")
        cycle_fill_pct = round(cycle_state["logical_bytes"] / CYCLE_RUNTIME_SOFT_TARGET_BYTES * 100)
        lines.extend(
            [
                "",
                "Current attempt runtime pressure",
                f"- {_plural(cycle_state['file_count'], 'regular file')}, "
                f"{_format_bytes(cycle_state['logical_bytes'])} / "
                f"{_format_bytes(CYCLE_RUNTIME_SOFT_TARGET_BYTES)} soft target "
                f"({cycle_fill_pct}%).",
                "- Contents left here survive failure; the controller removes only an empty "
                "directory shell and performs no automatic promotion.",
            ]
        )

    conditions: list[str] = []
    if latest_receipt and latest_receipt.get("status") != "completed":
        conditions.append(
            f"latest host receipt is {_short(latest_receipt.get('status') or 'unknown', 40)}"
        )
    if graph["current_missing_from_search"]:
        conditions.append(
            f"{graph['current_missing_from_search']:,} current memories are missing from search"
        )
    if over_budget:
        conditions.append(
            f"{_plural(len(over_budget), 'current memory', 'current memories')} "
            f"{'exceeds' if len(over_budget) == 1 else 'exceed'} the memory budget"
        )
    if workspace_overage > 0:
        conditions.append("owned workspace exceeds its soft target")
    if runtime_overage > 0:
        conditions.append("durable runtime exceeds its soft target")
    lines.extend(
        [
            "",
            "Conditions requiring attention",
            "- " + ("; ".join(conditions) if conditions else "none established by the host"),
            "- These are mechanical observations, not semantic verdicts. Unknown or partial "
            "measurements must not be reported as healthy.",
        ]
    )

    lines.extend(["", "## Latest attempt", "", "Latest operation"])
    latest_native_pointer: str | None = None
    if latest is None:
        lines.append("- No native Pi operation is recorded.")
    elif latest[0] == "receipt":
        receipt = latest[1]
        lines.append(
            f"- Host receipt {_short(receipt.get('id') or 'unknown', 120)} is the latest "
            f"operation verdict: status {_short(receipt.get('status') or 'unknown', 40)}, "
            f"started at {_short(receipt.get('started_at') or 'unknown', 100)}, completed at "
            f"{_short(receipt.get('completed_at') or 'unknown', 100)}."
        )
        if accepted and receipt.get("id") == accepted.get("id"):
            lines.append("- This is the accepted continuation above.")
        elif receipt.get("status") != "completed":
            lines.append("- This operation is not the accepted continuation.")
    else:
        session = latest[1]
        session_id = _short(session.get("id") or "unknown", 120)
        kind = _short(session.get("kind") or "session", 40)
        operation_id = _short(session.get("operation_id") or session_id, 120)
        latest_native_pointer = str(session.get("path") or "")
        lines.append(
            f"- Native {kind} session {session_id} is the latest operation: "
            f"status {_short(session.get('status') or 'unknown', 40)}, "
            f"operation {operation_id}, completed at "
            f"{_short(session.get('completed_at') or 'unknown', 100)}."
        )

        related_receipt = None
        if session.get("kind") == "synthesis":
            related_receipt = get_receipt(control_dir, str(session.get("operation_id") or ""))
            if related_receipt:
                lines.append(
                    f"- Host receipt {_short(related_receipt.get('id') or 'unknown', 120)} "
                    f"records status {_short(related_receipt.get('status') or 'unknown', 40)}."
                )
        if accepted and session.get("kind") == "synthesis":
            if session.get("operation_id") == accepted.get("id"):
                lines.append("- This is the accepted continuation above.")
            elif related_receipt and related_receipt.get("status") != "completed":
                lines.append("- This operation is not the accepted continuation.")

        runtime = " / ".join(
            _short(value, 80) for value in (session.get("provider"), session.get("model")) if value
        )
        if runtime:
            lines.append(f"- Runtime: {runtime}.")
        if session.get("error"):
            lines.append(f"- Recorded error: {_short(session['error'])}.")

    lines.extend(["", "## Drill-down routes", "", "Progressive evidence access"])
    if accepted:
        lines.append(
            f"- Accepted host receipt: "
            f"{_short(receipt_path(control_dir, str(accepted.get('id') or 'unknown')), 500)}."
        )
    if accepted_session:
        lines.append(f"- Native session: {_short(accepted_session.get('path') or 'unknown', 500)}.")
    if latest_native_pointer and (
        not accepted_session or latest_native_pointer != accepted_session.get("path")
    ):
        lines.append(f"- Latest native session: {_short(latest_native_pointer, 500)}.")
    lines.extend(
        [
            f"- Current graph and MEMEX: {_short(graph_path, 500)}.",
            f"- Owned files: {_short(workspace, 500)}.",
            "",
            "Scope",
            "- This is a bounded read-only projection of existing state, not another store.",
            "- Full transcript and tool evidence remains in the named native Pi session.",
            "- Exact MEMEX token count, encoding, and hard limit are shown in its header.",
            "- Workspace content is not interpreted here; only bounded mechanical "
            "pressure is shown.",
        ]
    )
    rendered = "\n".join(lines)
    token_estimate = (
        len(rendered.replace(_SELF_VIEW_USAGE_MARKER, "0")) + CHARS_PER_TOKEN - 1
    ) // CHARS_PER_TOKEN
    return rendered.replace(_SELF_VIEW_USAGE_MARKER, f"{token_estimate:,}")

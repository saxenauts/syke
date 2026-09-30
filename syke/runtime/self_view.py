"""Bounded, read-only orientation over Syke's existing self evidence."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke import __version__
from syke.control import list_receipts, receipt_path
from syke.db import SykeDB
from syke.memory.memex_budget import (
    MEMEX_TOKEN_LIMIT,
    MEMORY_TOKEN_LIMIT,
    count_memory_tokens,
    strip_memex_header,
)
from syke.observe.catalog import active_sources, discovered_roots
from syke.runtime.pi_sessions import find_session_by_id, find_session_by_name, list_sessions

WORKSPACE_SCAN_ENTRY_LIMIT = 10_000
WORKSPACE_SOFT_TARGET_BYTES = 3 * 1024 * 1024 * 1024
RUNTIME_SOFT_TARGET_BYTES = 3 * 1024 * 1024 * 1024
LISTED_ID_LIMIT = 6


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


def _local_time(value: object) -> str:
    """Render a recorded time in the host's zone at minute precision."""
    parsed = _parse_time(value)
    if parsed is None:
        return "an unknown time"
    local = parsed.astimezone()
    return f"{local.strftime('%Y-%m-%d %H:%M')} {local.tzname() or 'local'}"


def _sessions_path(
    workspace_root: Path,
    session_dir: Path | None,
) -> Path:
    if session_dir is not None:
        return session_dir.expanduser().resolve()
    return workspace_root.expanduser().resolve().parent / "control" / "sessions"


def _home_relative(path: str | Path) -> str:
    text = str(path)
    home = str(Path.home().expanduser().resolve())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + text[len(home) :]
    return text


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count:,} {noun}"


def _id_list(ids: list[str]) -> str:
    shown = ", ".join(f"`{_short(memory_id, 120)}`" for memory_id in ids[:LISTED_ID_LIMIT])
    more = len(ids) - LISTED_ID_LIMIT
    return f"{shown}, and {more:,} more" if more > 0 else shown


def _graph_condition(db: SykeDB, user_id: str) -> dict[str, Any]:
    graph = db.get_graph_stats(user_id)
    missing_from_search = [
        str(row[0])
        for row in db.conn.execute(
            """SELECT id FROM memories WHERE user_id = ?
               EXCEPT
               SELECT memory_id FROM memories_fts
               ORDER BY 1""",
            (user_id,),
        )
    ]
    unlinked = [
        str(row[0])
        for row in db.conn.execute(
            """SELECT m.id FROM memories AS m
               WHERE m.user_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM links AS l
                     WHERE l.user_id = m.user_id
                       AND (l.source_id = m.id OR l.target_id = m.id)
                 )
               ORDER BY m.id""",
            (user_id,),
        )
    ]
    return {
        "memories": int(graph["memories"] or 0),
        "links": int(graph["links"] or 0),
        "unlinked": unlinked,
        "missing_from_search": missing_from_search,
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


def _top_level_sizes(root: Path, excluded: set[str] | None = None) -> dict[str, Any]:
    """Size each top-level folder and the loose top-level files, within one entry bound.

    Symlinks are not followed and ``excluded`` absolute paths (the graph files)
    are not counted.
    """
    excluded = excluded or set()
    state: dict[str, Any] = {
        "dirs": {},
        "loose_count": 0,
        "loose_bytes": 0,
        "largest_loose": None,
        "total_bytes": 0,
        "errors": 0,
        "truncated": False,
        "exists": root.is_dir(),
    }
    if not state["exists"]:
        return state
    scanned = 0
    try:
        with os.scandir(root) as entries:
            top = sorted(entries, key=lambda entry: entry.name)
    except OSError:
        state["errors"] += 1
        return state

    pending: list[tuple[str, Path]] = []
    for entry in top:
        scanned += 1
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                state["dirs"][entry.name] = 0
                pending.append((entry.name, Path(entry.path)))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if os.path.abspath(entry.path) in excluded:
                continue
            size = int(entry.stat(follow_symlinks=False).st_size)
        except OSError:
            state["errors"] += 1
            continue
        state["loose_count"] += 1
        state["loose_bytes"] += size
        state["total_bytes"] += size
        largest = state["largest_loose"]
        if largest is None or size > largest[0]:
            state["largest_loose"] = (size, entry.name)

    while pending and not state["truncated"]:
        name, directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if scanned >= WORKSPACE_SCAN_ENTRY_LIMIT:
                        state["truncated"] = True
                        break
                    scanned += 1
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append((name, Path(entry.path)))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        size = int(entry.stat(follow_symlinks=False).st_size)
                    except OSError:
                        state["errors"] += 1
                        continue
                    state["dirs"][name] += size
                    state["total_bytes"] += size
        except OSError:
            state["errors"] += 1
    return state


def _workspace_line(workspace_state: dict[str, Any], runtime_state: dict[str, Any]) -> str:
    parts = [
        f"{_short(name, 80)}/ {_format_bytes(size)}"
        for name, size in workspace_state["dirs"].items()
    ]
    loose = _plural(workspace_state["loose_count"], "loose file")
    if workspace_state["loose_count"]:
        loose += f", {_format_bytes(workspace_state['loose_bytes'])}"
        largest = workspace_state["largest_loose"]
        if largest is not None and workspace_state["loose_count"] > 1:
            loose += f" (largest: {_short(largest[1], 80)} {_format_bytes(largest[0])})"
    listing = ", ".join(parts) if parts else "no folders"
    partial = (
        f" Sizes are partial: the scan stops at {WORKSPACE_SCAN_ENTRY_LIMIT:,} entries."
        if workspace_state["truncated"] or runtime_state["truncated"]
        else ""
    )
    return (
        f"- Workspace top level: {listing}; {loose}. "
        f"Runtime: {_format_bytes(runtime_state['total_bytes'])}.{partial}"
    )


def _source_inventory_lines(
    workspace: Path,
    *,
    home: Path | None,
    selected_sources: tuple[str, ...] | None,
) -> list[str]:
    selected = set(selected_sources) if selected_sources is not None else None
    adapters = workspace / "adapters"
    lines: list[str] = []
    known: set[str] = set()
    for spec in active_sources():
        known.add(spec.source)
        if selected is not None and spec.source not in selected:
            continue
        root_parts: list[str] = []
        for root in discovered_roots(spec, home=home):
            path = Path(root).expanduser().resolve()
            readable = path.exists() and os.access(path, os.R_OK)
            root_parts.append(f"{path}" if readable else f"{path} (unavailable)")
        roots_text = "; ".join(root_parts) if root_parts else "no roots found"
        adapter = adapters / f"{spec.source}.md"
        guide = "" if adapter.is_file() else " (no adapter guide)"
        lines.append(f"- {spec.source}: {roots_text}{guide}")
    if not lines:
        lines.append("- No external sources are selected.")
    try:
        extra = sorted(
            path.stem for path in adapters.glob("*.md") if path.is_file() and path.stem not in known
        )
    except OSError:
        extra = []
    if extra:
        lines.append(f"- Other adapter guides in adapters/: {', '.join(extra)}.")
    return lines


def _operating_notes_line(workspace: Path) -> str:
    from syke.memory.learned import (
        OPERATING_NOTES_TOKEN_LIMIT,
        measure_learned_projection,
        operating_notes_path,
    )

    path = operating_notes_path(workspace)
    try:
        body = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"- Operating notes: {path} does not exist yet. It is yours to write."
    except (OSError, UnicodeDecodeError):
        return f"- Operating notes: {path} could not be read."
    tokens = int(measure_learned_projection(body)["tokens"])
    return (
        f"- Operating notes: {path}, {tokens:,} tokens; the prompt shows about the first "
        f"{OPERATING_NOTES_TOKEN_LIMIT:,}. It is yours to prune."
    )


def _no_access_paths() -> list[str]:
    """Paths the sandbox profile denies, derived from the profile inputs."""
    from syke.runtime.child_env import child_temp_paths
    from syke.runtime.sandbox import _credential_deny_paths

    denied = [_home_relative(path) for path in _credential_deny_paths()]
    # /tmp is outside every allow rule unless $TMPDIR itself lives there.
    shared_tmp = Path("/private/tmp")
    temp_roots = [Path(path).resolve() for path in child_temp_paths()]
    if not any(root.is_relative_to(shared_tmp) for root in temp_roots):
        denied.append("/tmp")
    return denied


def _session_for_receipt(sessions_path: Path, receipt: dict[str, Any]) -> dict[str, Any] | None:
    session_id = receipt.get("session_id")
    session = (
        find_session_by_id(sessions_path, str(session_id))
        if isinstance(session_id, str) and session_id
        else None
    )
    if session is None:
        session = find_session_by_name(
            sessions_path, f"syke:synthesis:{receipt.get('id') or 'unknown'}"
        )
    return session


def _count_files(path: Path, limit: int = 1_000) -> int:
    count = 0
    try:
        for _root, _dirs, files in os.walk(path):
            count += len(files)
            if count >= limit:
                break
    except OSError:
        return count
    return count


def _duration_seconds(receipt: dict[str, Any]) -> int | None:
    started = _parse_time(receipt.get("started_at"))
    completed = _parse_time(receipt.get("completed_at"))
    if started is None or completed is None:
        return None
    return max(0, round((completed - started).total_seconds()))


def _recent_runs_lines(
    *,
    control_dir: Path,
    sessions_path: Path,
    runtime_root: Path,
) -> tuple[list[str], dict[str, Any] | None]:
    """Return the recent-run lines and the latest wake receipt when it did not complete."""
    lines: list[str] = []
    completed = list_receipts(control_dir, status="completed", limit=1)
    last_completed = completed[0] if completed else None
    latest_all = list_receipts(control_dir, limit=1)
    latest_receipt = latest_all[0] if latest_all else None

    if last_completed:
        completed_id = str(last_completed.get("id") or "unknown")
        duration = _duration_seconds(last_completed)
        after = f" after {duration:,} s" if duration is not None else ""
        memex = "changed" if last_completed.get("memex_updated") else "unchanged"
        session = _session_for_receipt(sessions_path, last_completed)
        session_text = (
            f" Session: {_short(session.get('path'), 500)}."
            if session
            else f" Session: none found named syke:synthesis:{_short(completed_id, 120)}."
        )
        lines.append(
            f"- Last completed wake: {_short(completed_id, 120)}, finished "
            f"{_local_time(last_completed.get('completed_at'))}{after}; MEMEX {memex}. "
            f"Receipt: {receipt_path(control_dir, completed_id)}.{session_text} "
            "The receipt does not list graph changes; the session does."
        )
    else:
        lines.append("- No wake has completed yet.")

    failed_latest: dict[str, Any] | None = None
    if latest_receipt and latest_receipt.get("status") != "completed":
        failed_latest = latest_receipt
        failed_id = str(latest_receipt.get("id") or "unknown")
        status = _short(latest_receipt.get("status") or "unknown", 40)
        error = latest_receipt.get("error")
        error_text = f": {_short(error)}" if error else ""
        recovery = latest_receipt.get("recovery")
        rolled_back = (
            " Its graph and MEMEX changes were rolled back."
            if isinstance(recovery, dict) and recovery.get("restored") is True
            else ""
        )
        session = _session_for_receipt(sessions_path, latest_receipt)
        session_text = f" Session: {_short(session.get('path'), 500)}." if session else ""
        folder = runtime_root / "cycles" / failed_id
        folder_text = (
            f" Its run folder: {folder} ({_plural(_count_files(folder), 'file')})."
            if folder.is_dir()
            else ""
        )
        lines.append(
            f"- Latest wake {_short(failed_id, 120)} ended {status} at "
            f"{_local_time(latest_receipt.get('completed_at'))}{error_text}.{rolled_back} "
            f"Receipt: {receipt_path(control_dir, failed_id)}.{session_text}{folder_text}"
        )

    latest_receipt_time = _parse_time(
        (latest_receipt or {}).get("completed_at") or (latest_receipt or {}).get("started_at")
    )
    asks = list_sessions(sessions_path, kind="ask", limit=1)
    latest_ask = asks[0] if asks else None
    if latest_ask:
        ask_time = _parse_time(latest_ask.get("completed_at") or latest_ask.get("started_at"))
        if latest_receipt_time is None or (ask_time is not None and ask_time > latest_receipt_time):
            ask_id = _short(
                latest_ask.get("operation_id") or latest_ask.get("id") or "unknown", 120
            )
            status = _short(latest_ask.get("status") or "unknown", 40)
            error = latest_ask.get("error")
            error_text = f": {_short(error)}" if error else ""
            lines.append(
                f"- Latest ask {ask_id} at "
                f"{_local_time(latest_ask.get('completed_at') or latest_ask.get('started_at'))}, "
                f"{status}{error_text}. Session: {_short(latest_ask.get('path'), 500)}."
            )
    return lines, failed_latest


def _model_text(sessions_path: Path) -> str:
    recent = list_sessions(sessions_path, limit=1)
    if not recent:
        return "unknown"
    latest = recent[0]
    parts = [_short(value, 80) for value in (latest.get("provider"), latest.get("model")) if value]
    return " / ".join(parts) or "unknown"


def build_self_view(
    db: SykeDB,
    user_id: str,
    *,
    workspace_root: Path,
    session_dir: Path | None = None,
    cycle_runtime: Path | None = None,
    context: str = "synthesis",
    home: Path | None = None,
    selected_sources: tuple[str, ...] | None = None,
) -> str:
    """Render the current self-observation projection without storing it.

    Each fact has one owner: this run's kind, id, reference time and time limit
    live in the run block, and the MEMEX size lives in the MEMEX header.
    """
    del context  # Every run sees the same facts; the run block names the kind.
    workspace = workspace_root.expanduser().resolve()
    graph_path = _path_text(db.db_path)
    sessions_path = _sessions_path(workspace, session_dir)
    control_dir = sessions_path.parent
    runtime_root = control_dir / "runtime"
    from syke.config import DAEMON_INTERVAL
    from syke.llm.backends.pi_synthesis import MAX_ACCEPTANCE_REPAIR_PROMPTS
    from syke.runtime.sandbox import sandbox_enabled, sandbox_read_paths

    installed_core = Path(__file__).resolve().parents[1]
    memex = db.get_memex(user_id)
    memex_id = _short(memex.get("id") if memex else "none", 120)

    lines = [
        "# Self-observation",
        "",
        "## Syke",
        "",
        f"- Person: {_short(user_id, 120)}. Syke {_short(__version__, 80)}, installed read-only "
        f"at {installed_core}. Model: {_model_text(sessions_path)}. Wakes are scheduled every "
        f"{max(1, DAEMON_INTERVAL // 60):,} minutes.",
    ]
    if cycle_runtime is not None:
        lines.append(
            f"- This run's folder: {cycle_runtime.expanduser().resolve()} (empty now; kept "
            "after the run if anything is left in it)."
        )
    lines.append(
        f"- This run's session is being written now as the newest file in {sessions_path}."
    )
    lines.append(_operating_notes_line(workspace))

    read_roots = sandbox_read_paths()
    computer_home = str(Path.home().expanduser().resolve())
    read_text = (
        f"{computer_home} ($HOME)"
        if read_roots == (computer_home,)
        else "; ".join(_short(root, 500) for root in read_roots) or "no computer roots"
    )
    protected = ", ".join(
        str(control_dir / name) for name in ("sessions", "receipts", "records", "recovery")
    )
    lines.extend(
        [
            "",
            "## Paths",
            "",
            f"- Writable: {workspace} (workspace) and {runtime_root} (runtime; earlier runs' "
            "folders are under cycles/).",
            f"- Readable, not writable: {read_text}, installed Syke, $TMPDIR, and Syke's "
            f"protected evidence: {protected}.",
            f"- No access: {', '.join(_no_access_paths())}.",
        ]
    )
    if not sandbox_enabled():
        lines.append(
            "- The OS sandbox is off: tools have the running process's permissions, so the "
            "lines above are policy, not enforced."
        )
    lines.append("- Outbound network is open.")

    excluded: set[str] = set()
    if graph_path != ":memory:":
        suffixes = ("-journal", "-shm", "-wal", ".lock")
        excluded = {graph_path, *(f"{graph_path}{suffix}" for suffix in suffixes)}
    workspace_state = _top_level_sizes(workspace, excluded)
    runtime_state = _top_level_sizes(runtime_root)
    lines.append(_workspace_line(workspace_state, runtime_state))
    workspace_over = workspace_state["total_bytes"] > WORKSPACE_SOFT_TARGET_BYTES
    runtime_over = runtime_state["total_bytes"] > RUNTIME_SOFT_TARGET_BYTES
    if workspace_over:
        lines.append(
            f"- Workspace is {_format_bytes(workspace_state['total_bytes'])}, over its "
            f"{_format_bytes(WORKSPACE_SOFT_TARGET_BYTES)} soft target. Nothing is deleted "
            "automatically."
        )
    if runtime_over:
        lines.append(
            f"- Runtime is {_format_bytes(runtime_state['total_bytes'])}, over its "
            f"{_format_bytes(RUNTIME_SOFT_TARGET_BYTES)} soft target. Nothing is deleted "
            "automatically."
        )
    unreadable = workspace_state["errors"] + runtime_state["errors"]
    if unreadable:
        lines.append(f"- {_plural(unreadable, 'entry', 'entries')} could not be read while sizing.")

    lines.extend(
        [
            "",
            f"Sources (adapter guide in {workspace / 'adapters'}/<source>.md; native records "
            "at the roots):",
            *_source_inventory_lines(workspace, home=home, selected_sources=selected_sources),
        ]
    )

    graph = _graph_condition(db, user_id)
    missing = graph["missing_from_search"]
    over_budget = _memories_over_budget(db, user_id)
    lines.extend(
        [
            "",
            "## Graph",
            "",
            f"- {graph_path}, bound to {_short(user_id, 120)}; that identity is fixed. Read and "
            "write it with sqlite3 through `bash`; there is no separate graph tool.",
            "- `memories(id TEXT PRIMARY KEY, user_id TEXT, content TEXT, created_at TEXT, "
            "updated_at TEXT)`; every row is current.",
            "- `links(id TEXT PRIMARY KEY, user_id TEXT, source_id TEXT, target_id TEXT, "
            "reason TEXT, created_at TEXT)`",
            "- `current_memex(singleton INTEGER PRIMARY KEY, id TEXT, user_id TEXT, content TEXT, "
            f"created_at TEXT, updated_at TEXT)`: one row, {memex_id}. The host keeps MEMEX "
            "history outside syke.db; don't add history rows.",
            "- `memories_fts(memory_id, content)`: FTS5, kept in step with `memories`. Full-text "
            "search is `MATCH` on `memories_fts` joined on `memories_fts.memory_id = "
            "memories.id`; `memories.content MATCH` does not work.",
            f"- Each memory has a {MEMORY_TOKEN_LIMIT:,}-token budget (o200k_base). A memory "
            "created or revised over it is rejected; one already over may shrink or hold, not "
            "grow.",
            "- Resolve a shortened ID to the full ID before using it to change a row or as a "
            "link endpoint.",
            "- After a wake the host checks: SQLite integrity; the single identity; existing "
            "memories, links and the MEMEX row keep their id and created_at; every link has an "
            "id, a reason and two existing memory ids, so delete a memory's links before the "
            f"memory; memory budgets; MEMEX within {MEMEX_TOKEN_LIMIT:,} tokens; "
            "`memories_fts` matches `memories`. On rejection it restores syke.db and sends the "
            f"issues back, up to {MAX_ACCEPTANCE_REPAIR_PROMPTS} times within the same time "
            "limit.",
            "- If syke.db doesn't match this description, stop changing it and write down the "
            "mismatch.",
        ]
    )
    now_line = f"- Now: {graph['memories']:,} memories, {graph['links']:,} links."
    if graph["unlinked"]:
        now_line += f" Unlinked: {_id_list(graph['unlinked'])}."
    lines.append(now_line)
    if over_budget:
        shown = ", ".join(
            f"`{_short(memory_id, 120)}` {tokens:,}"
            for memory_id, tokens in over_budget[:LISTED_ID_LIMIT]
        )
        more = len(over_budget) - LISTED_ID_LIMIT
        lines.append(
            f"- Over budget: {len(over_budget):,} of {graph['memories']:,} memories "
            f"{'exceeds' if len(over_budget) == 1 else 'exceed'} {MEMORY_TOKEN_LIMIT:,} tokens: "
            f"{shown}{f', and {more:,} more' if more > 0 else ''}."
        )
    if graph["missing_from_search"]:
        lines.append(
            f"- Missing from search: {_plural(len(missing), 'memory', 'memories')}: "
            f"{_id_list(missing)}."
        )
    memex_path = workspace / "MEMEX.md"
    if memex and memex_path.is_file():
        try:
            projected = strip_memex_header(memex_path.read_text(encoding="utf-8").strip())
            if projected.strip() != str(memex.get("content") or "").strip():
                lines.append(
                    f"- {memex_path} differs from the current_memex row; the prompt's MEMEX "
                    "block shows the row."
                )
        except OSError:
            pass

    recent_lines, failed_latest = _recent_runs_lines(
        control_dir=control_dir,
        sessions_path=sessions_path,
        runtime_root=runtime_root,
    )
    lines.extend(["", "## Recent runs", "", *recent_lines])

    attention: list[str] = []
    if failed_latest is not None:
        attention.append(
            f"latest wake ended {_short(failed_latest.get('status') or 'unknown', 40)}"
        )
    if over_budget:
        attention.append(f"{_plural(len(over_budget), 'memory', 'memories')} over budget")
    if graph["missing_from_search"]:
        attention.append(f"{_plural(len(missing), 'memory', 'memories')} missing from search")
    if workspace_over:
        attention.append("workspace over soft target")
    if runtime_over:
        attention.append("runtime over soft target")
    lines.extend(["", f"Needs attention: {'; '.join(attention) if attention else 'none'}."])
    return "\n".join(lines)

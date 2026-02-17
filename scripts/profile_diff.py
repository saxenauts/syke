#!/usr/bin/env python3
"""Compare the two most recent Syke profiles and output a markdown diff."""

from __future__ import annotations

import sys

from syke.config import user_db_path
from syke.db import SykeDB
from syke.models import UserProfile


def get_two_latest_profiles(user_id: str) -> tuple[UserProfile | None, UserProfile | None]:
    """Return (older, newer) profiles, or (None, newer) if only one exists."""
    db = SykeDB(user_db_path(user_id))
    rows = db.conn.execute(
        "SELECT profile_json FROM profiles WHERE user_id = ? ORDER BY created_at DESC LIMIT 2",
        (user_id,),
    ).fetchall()
    db.close()
    if not rows:
        return None, None
    newer = UserProfile.model_validate_json(rows[0][0])
    older = UserProfile.model_validate_json(rows[1][0]) if len(rows) > 1 else None
    return older, newer


def diff_threads(
    old: list[dict], new: list[dict]
) -> tuple[list[str], list[str], list[str]]:
    """Return (added, removed, changed) thread names."""
    old_map = {t["name"]: t for t in old}
    new_map = {t["name"]: t for t in new}
    added = [n for n in new_map if n not in old_map]
    removed = [n for n in old_map if n not in new_map]
    changed = []
    for name in new_map:
        if name in old_map:
            o, n = old_map[name], new_map[name]
            diffs = []
            if o.get("intensity") != n.get("intensity"):
                diffs.append(f"intensity {o.get('intensity')} → {n.get('intensity')}")
            old_sigs = set(o.get("recent_signals", []))
            new_sigs = set(n.get("recent_signals", []))
            new_signals = new_sigs - old_sigs
            if new_signals:
                diffs.append(f"+{len(new_signals)} new signals")
            if diffs:
                changed.append(f"{name}: {', '.join(diffs)}")
    return added, removed, changed


def main() -> None:
    user_id = sys.argv[1] if len(sys.argv) > 1 else "default"
    older, newer = get_two_latest_profiles(user_id)

    if newer is None:
        print(f"No profiles found for user '{user_id}'.")
        sys.exit(1)

    print(f"# Profile Diff — {user_id}\n")

    if older is None:
        print("Only one profile exists. No diff available.")
        print(f"\n**Current**: {newer.events_count} events from {', '.join(newer.sources)}")
        sys.exit(0)

    # Event count delta
    delta = newer.events_count - older.events_count
    sign = "+" if delta > 0 else ""
    print(f"## Events\n{older.events_count} → {newer.events_count} ({sign}{delta})\n")

    # Sources
    old_src = set(older.sources)
    new_src = set(newer.sources)
    added_src = new_src - old_src
    if added_src:
        print(f"## New Sources\n{', '.join(added_src)}\n")

    # Active threads diff
    old_threads = [t.model_dump() for t in older.active_threads]
    new_threads = [t.model_dump() for t in newer.active_threads]
    added, removed, changed = diff_threads(old_threads, new_threads)

    if added or removed or changed:
        print("## Active Threads\n")
        for name in added:
            print(f"- **+ {name}**")
        for name in removed:
            print(f"- **- {name}**")
        for desc in changed:
            print(f"- ~ {desc}")
        print()

    # Identity anchor
    if older.identity_anchor != newer.identity_anchor:
        print("## Identity Anchor Changed\n")
        # Show first 200 chars of each
        print(f"**Before**: {older.identity_anchor[:200]}...")
        print(f"**After**: {newer.identity_anchor[:200]}...")
        print()

    # Cost
    print(f"## Perception Cost\n${older.cost_usd:.4f} → ${newer.cost_usd:.4f}")


if __name__ == "__main__":
    main()

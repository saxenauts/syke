#!/usr/bin/env python3
"""Sandbox runner — entry point for containerized synthesis replay.

Reads config from environment variables. Runs the replay loop.
Writes results to /output. No host filesystem access beyond mounts.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

# Add workspace to path for syke imports
sys.path.insert(0, "/workspace")


def main() -> None:
    from replay import run_replay

    # Config from environment
    frozen_db = Path("/data/frozen.db")
    output_dir = Path("/output")
    skill_file = Path("/skill.md")

    if not frozen_db.exists():
        raise SystemExit("No frozen DB at /data/frozen.db — mount it with -v")

    user_id = os.environ.get("REPLAY_USER_ID", "replay")
    source_user_id = os.environ.get("REPLAY_SOURCE_USER_ID", "fresh_test")
    start_day = os.environ.get("REPLAY_START_DAY") or None
    max_days_str = os.environ.get("REPLAY_MAX_DAYS")
    max_days = int(max_days_str) if max_days_str else None

    # Read skill file if mounted (not /dev/null)
    skill_path = skill_file if skill_file.exists() and skill_file.stat().st_size > 0 else None

    # Compute frozen DB checksum for reproducibility
    db_hash = hashlib.sha256(frozen_db.read_bytes()).hexdigest()
    print(f"Frozen DB: {frozen_db} (sha256:{db_hash[:16]})")
    print(f"User: {user_id} (source: {source_user_id})")
    print(f"Window: start={start_day or 'beginning'}, max_days={max_days or 'all'}")
    print(f"Skill: {skill_path or '(none — zero prompt)'}")
    print(f"Model: {os.environ.get('SYKE_SYNC_MODEL', 'default')}")
    print()

    result = run_replay(
        source_db_path=frozen_db,
        output_dir=output_dir,
        user_id=user_id,
        source_user_id=source_user_id,
        dry_run=False,
        max_days=max_days,
        start_day=start_day,
        condition="production",
        skill_file=skill_path,
    )

    # Add sandbox metadata
    results_path = output_dir / "replay_results.json"
    if results_path.exists():
        data = json.loads(results_path.read_text())
        data["metadata"]["sandbox"] = {
            "containerized": True,
            "frozen_db_sha256": db_hash,
            "python_version": sys.version,
            "skill_file": str(skill_path) if skill_path else None,
        }
        results_path.write_text(json.dumps(data, indent=2))

    print("\nDone. Results at /output/replay_results.json")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Helpers for naming replay/eval runs and composing eval packets.

Two workflows:

1. Suggest a standardized run directory name using an ablation number + label.
2. Upsert a packet entry in runs/eval_manifest.json, including composed packets
   built from multiple source runs.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LAB_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = LAB_ROOT / "runs"
EVAL_MANIFEST_PATH = RUNS_ROOT / "eval_manifest.json"


def _slugify(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower()).strip("-")
    return value or "run"


def _ablation_base(ablation: int, label: str) -> str:
    if ablation < 0:
        raise ValueError("ablation number must be non-negative")
    return f"ab{ablation:02d}-{_slugify(label)}"


def build_run_slug(*, ablation: int, label: str, kind: str, stamp: str | None = None) -> str:
    run_kind = kind.strip().lower()
    if run_kind not in {"replay", "eval"}:
        raise ValueError("kind must be replay or eval")
    ts = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{_ablation_base(ablation, label)}-{run_kind}-{ts}"


def _load_eval_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_doc": "Eval manifest.", "packets": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload.setdefault("_doc", "Eval manifest.")
        packets = payload.get("packets")
        if not isinstance(packets, list):
            payload["packets"] = []
        return payload
    raise ValueError(f"Unexpected eval manifest shape in {path}")


def _write_eval_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _relative_run_path(run_dir: Path) -> str:
    resolved = run_dir.resolve()
    runs_root = RUNS_ROOT.resolve()
    try:
        rel = resolved.relative_to(runs_root)
    except ValueError as exc:
        raise ValueError(f"Run dir must live under {runs_root}: {resolved}") from exc
    return f"./runs/{rel.as_posix()}"


def _validate_run_dir(run_dir: Path) -> None:
    if not run_dir.exists():
        raise ValueError(f"Run dir not found: {run_dir}")
    bench = run_dir / "benchmark_results.json"
    if not bench.exists():
        raise ValueError(f"Run dir is missing benchmark_results.json: {run_dir}")


def _packet_name_from_args(name: str | None, ablation: int | None, label: str | None) -> str:
    if name:
        return name
    if ablation is not None and label:
        return _ablation_base(ablation, label)
    raise ValueError("Provide --name or both --ablation and --label")


def _parse_condition_entry(raw: str) -> dict[str, str]:
    if "=" not in raw:
        raise ValueError(
            f"Invalid --condition value {raw!r}; expected alias=run_dir or alias=run_dir@source_condition"
        )
    alias, rest = raw.split("=", 1)
    alias = alias.strip()
    if not alias:
        raise ValueError(f"Invalid --condition value {raw!r}; alias is empty")
    if "@" in rest:
        run_part, source_condition = rest.rsplit("@", 1)
        source_condition = source_condition.strip()
    else:
        run_part, source_condition = rest, ""
    run_dir = Path(run_part.strip()).resolve()
    _validate_run_dir(run_dir)
    entry = {"name": alias, "source": _relative_run_path(run_dir)}
    if source_condition and source_condition != alias:
        entry["source_condition"] = source_condition
    return entry


def upsert_packet(
    *,
    manifest_path: Path,
    name: str,
    description: str | None,
    created_at: str,
    run_dir: Path | None,
    conditions: list[dict[str, str]],
    require_pure: bool,
) -> dict[str, Any]:
    manifest = _load_eval_manifest(manifest_path)
    packets = [p for p in manifest.get("packets", []) if p.get("name") != name]

    if run_dir and conditions:
        raise ValueError("Use either --run-dir or --condition, not both")
    if not run_dir and not conditions:
        raise ValueError("Provide --run-dir for a single-run packet or at least one --condition")

    packet: dict[str, Any] = {"name": name, "created_at": created_at}
    if description:
        packet["description"] = description

    if run_dir:
        run_dir = run_dir.resolve()
        _validate_run_dir(run_dir)
        packet["path"] = _relative_run_path(run_dir)
    else:
        if require_pure and not any(cond.get("name") == "pure" for cond in conditions):
            raise ValueError("Composed packets must include a pure condition unless --allow-no-pure is set")
        packet["conditions"] = conditions

    packets.append(packet)
    manifest["packets"] = packets
    _write_eval_manifest(manifest_path, manifest)
    return packet


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Name replay/eval runs and compose eval packets")
    sub = parser.add_subparsers(dest="command", required=True)

    suggest = sub.add_parser("suggest-run-name", help="Suggest a standardized ablation run path")
    suggest.add_argument("--ablation", type=int, required=True, help="Parallel ablation number")
    suggest.add_argument("--label", required=True, help="Human-readable ablation label")
    suggest.add_argument("--kind", choices=["replay", "eval"], required=True, help="Run kind")
    suggest.add_argument("--stamp", help="Override UTC stamp (default: now)")

    packet = sub.add_parser("upsert-packet", help="Create or replace an eval packet entry")
    packet.add_argument("--name", help="Packet name (optional when using --ablation + --label)")
    packet.add_argument("--ablation", type=int, help="Parallel ablation number for generated packet name")
    packet.add_argument("--label", help="Human-readable label for generated packet name")
    packet.add_argument("--description", help="Packet description")
    packet.add_argument(
        "--created-at",
        default="now",
        help="Packet created_at timestamp (default: now)",
    )
    packet.add_argument("--manifest", default=str(EVAL_MANIFEST_PATH), help="Path to eval_manifest.json")
    packet.add_argument("--run-dir", help="Single-run packet source directory")
    packet.add_argument(
        "--condition",
        action="append",
        default=[],
        help="Composed packet member: alias=run_dir or alias=run_dir@source_condition",
    )
    packet.add_argument(
        "--allow-no-pure",
        action="store_true",
        help="Allow composed packets that do not include a pure baseline",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.command == "suggest-run-name":
        slug = build_run_slug(
            ablation=args.ablation,
            label=args.label,
            kind=args.kind,
            stamp=args.stamp,
        )
        payload = {"slug": slug, "path": str((RUNS_ROOT / slug).resolve())}
        print(json.dumps(payload, indent=2))
        return

    if args.command == "upsert-packet":
        created_at = (
            datetime.now(UTC).isoformat() if args.created_at == "now" else str(args.created_at)
        )
        name = _packet_name_from_args(args.name, args.ablation, args.label)
        conditions = [_parse_condition_entry(raw) for raw in args.condition]
        run_dir = Path(args.run_dir).resolve() if args.run_dir else None
        packet = upsert_packet(
            manifest_path=Path(args.manifest).resolve(),
            name=name,
            description=args.description,
            created_at=created_at,
            run_dir=run_dir,
            conditions=conditions,
            require_pure=not args.allow_no_pure,
        )
        print(json.dumps(packet, indent=2))
        return

    raise SystemExit(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()

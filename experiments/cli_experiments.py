"""Experiment CLI commands — auto-registered when experiments/ is available."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from syke.config import user_data_dir, user_profile_path
from syke.db import SykeDB

console = Console()


def _get_db(user_id: str) -> SykeDB:
    from syke.config import user_db_path
    db = SykeDB(user_db_path(user_id))
    db.initialize()
    return db


def register_experiment_commands(cli: click.Group) -> None:
    """Register all experiment commands onto the main CLI group."""

    @cli.command(hidden=True)
    @click.option("--live", is_flag=True, help="Use real Opus 4.6 calls (costs ~$2-5)")
    @click.option("--mock", is_flag=True, help="Use mock responses (free, for dev)")
    @click.option("--save", is_flag=True, help="Save report to data/{user}/benchmark_report.md")
    @click.option("--trace", is_flag=True, help="Write full JSONL traces to data/{user}/traces/")
    @click.option(
        "--methods", default=None,
        help="Comma-separated methods to compare (legacy,agentic,agentic-v2). Default: legacy,agentic",
    )
    @click.pass_context
    def benchmark(ctx: click.Context, live: bool, mock: bool, save: bool, trace: bool, methods: str | None) -> None:
        """Compare perception methods on the same data.

        Default: mock mode (no API cost). Use --live for real Opus 4.6 calls.
        Use --methods legacy,agentic,agentic-v2 for 3-way comparison.
        Use --trace to capture full JSONL trace files for analysis.
        """
        import shutil

        from experiments.benchmarking.benchmark import run_benchmark, run_mock_benchmark
        from syke.metrics import MetricsTracker

        user_id = ctx.obj["user"]
        db = _get_db(user_id)
        tracker = MetricsTracker(user_id)

        method_list = [m.strip() for m in methods.split(",")] if methods else ["legacy", "agentic"]
        include_v2 = "agentic-v2" in method_list

        try:
            events_count = db.count_events(user_id)
            sources = db.get_sources(user_id)
            if events_count == 0:
                console.print("[yellow]No events found. Run: syke setup or syke simulate first.[/yellow]")
                return

            use_live = live and not mock
            mode_label = "live (Opus 4.6)" if use_live else "mock (no API cost)"
            console.print(f"\n[bold]Perception Benchmark[/bold] — user: [cyan]{user_id}[/cyan]")
            console.print(f"  Mode: {mode_label}")
            console.print(f"  Methods: {', '.join(method_list)}")
            console.print(f"  Events: {events_count} across {len(sources)} sources")
            if trace:
                console.print(f"  Trace: [green]enabled[/green]")
            console.print()

            # Prepare trace directory
            trace_dir = None
            if trace and use_live:
                trace_dir = user_data_dir(user_id) / "traces"
                if trace_dir.exists():
                    shutil.rmtree(trace_dir)
                trace_dir.mkdir(parents=True, exist_ok=True)

            if use_live:
                console.print("[bold]Running perception methods...[/bold]")
                with tracker.track("benchmark_live") as metrics:
                    report = run_benchmark(
                        db, user_id, live_console=console, methods=method_list,
                        trace_dir=trace_dir,
                    )
                    total_cost = report.legacy.cost_usd + report.agentic.cost_usd
                    if report.agentic_v2:
                        total_cost += report.agentic_v2.cost_usd
                    metrics.cost_usd = total_cost
                    metrics.events_processed = events_count
            else:
                report = run_mock_benchmark(db, user_id, include_v2=include_v2)

            # Display comparison table — 2-way or 3-way
            has_v2 = report.agentic_v2 is not None
            title = "Legacy vs Agentic vs Multi-Agent Perception" if has_v2 else "Legacy vs Agentic Perception"
            table = Table(title=title)
            table.add_column("Metric", style="cyan", width=28)
            table.add_column("Legacy", justify="right", style="yellow", width=16)
            table.add_column("Agentic", justify="right", style="green", width=16)
            if has_v2:
                table.add_column("Multi-Agent v2", justify="right", style="bold magenta", width=16)

            leg, a = report.legacy, report.agentic
            v2 = report.agentic_v2
            q = report.quality
            vq = report.agentic_v2_quality

            def _row(label: str, l_val: str, a_val: str, v2_val: str | None = None) -> None:
                args = [label, l_val, a_val]
                if has_v2:
                    args.append(v2_val or "-")
                table.add_row(*args)

            _row("Input tokens", f"{leg.input_tokens:,}", f"{a.input_tokens:,}",
                 f"{v2.input_tokens:,}" if v2 else None)
            _row("Output tokens", f"{leg.output_tokens:,}", f"{a.output_tokens:,}",
                 f"{v2.output_tokens:,}" if v2 else None)
            _row("Thinking tokens", f"{leg.thinking_tokens:,}", f"{a.thinking_tokens:,}",
                 f"{v2.thinking_tokens:,}" if v2 else None)
            _row("Cost USD", f"${leg.cost_usd:.4f}", f"${a.cost_usd:.4f}",
                 f"${v2.cost_usd:.4f}" if v2 else None)
            _row("Wall time (s)", f"{leg.wall_time_seconds:.1f}", f"{a.wall_time_seconds:.1f}",
                 f"{v2.wall_time_seconds:.1f}" if v2 else None)
            _row("API turns", str(leg.num_turns), str(a.num_turns),
                 str(v2.num_turns) if v2 else None)
            _row("", "", "", "" if has_v2 else None)
            _row("Active threads", str(q.legacy_thread_count), str(q.agentic_thread_count),
                 str(vq.thread_count) if vq else None)
            _row("Cross-platform threads", str(q.legacy_cross_platform_threads), str(q.agentic_cross_platform_threads),
                 str(vq.cross_platform_threads) if vq else None)
            _row("Source coverage", f"{q.legacy_source_coverage:.0%}", f"{q.agentic_source_coverage:.0%}",
                 f"{vq.source_coverage:.0%}" if vq else None)
            _row("Identity anchor len", str(q.legacy_identity_anchor_len), str(q.agentic_identity_anchor_len),
                 str(vq.identity_anchor_len) if vq else None)
            _row("Voice patterns", str(q.legacy_has_voice_patterns), str(q.agentic_has_voice_patterns),
                 str(vq.has_voice_patterns) if vq else None)
            _row("Fields populated", str(q.legacy_fields_populated), str(q.agentic_fields_populated),
                 str(vq.fields_populated) if vq else None)

            console.print(table)

            # Token/cost savings
            if leg.input_tokens > 0:
                savings = (1 - a.input_tokens / leg.input_tokens) * 100
                console.print(f"\n  Agentic input token savings: [green]{savings:.0f}%[/green]")
            if leg.cost_usd > 0:
                cost_savings = (1 - a.cost_usd / leg.cost_usd) * 100
                console.print(f"  Agentic cost savings: [green]{cost_savings:.0f}%[/green]")
            if v2 and leg.cost_usd > 0:
                v2_cost_savings = (1 - v2.cost_usd / leg.cost_usd) * 100
                console.print(f"  Multi-Agent v2 cost savings: [bold magenta]{v2_cost_savings:.0f}%[/bold magenta]")

            # Per-tool performance tables
            for label, result in [("Agentic", a), ("Multi-Agent v2", v2)]:
                if result is None or not result.tool_perf:
                    continue
                perf_table = Table(title=f"{label} Tool Performance")
                perf_table.add_column("Tool", style="cyan", width=22)
                perf_table.add_column("Calls", justify="right", width=6)
                perf_table.add_column("Avg Latency (ms)", justify="right", width=16)
                perf_table.add_column("Empty Rate", justify="right", width=10)
                perf_table.add_column("Total Result (KB)", justify="right", width=16)
                for row in result.tool_perf:
                    perf_table.add_row(
                        row["tool"],
                        str(row["calls"]),
                        str(row["avg_latency_ms"]),
                        f"{row['empty_rate']:.0%}",
                        str(row["total_result_kb"]),
                    )
                console.print(perf_table)

            # Identity anchors
            console.print(f"\n[bold]Identity Anchors[/bold]")
            console.print(f"  [yellow]Legacy:[/yellow]     {leg.profile.identity_anchor[:200]}")
            console.print(f"  [green]Agentic:[/green]    {a.profile.identity_anchor[:200]}")
            if v2:
                console.print(f"  [magenta]Multi-v2:[/magenta]  {v2.profile.identity_anchor[:200]}")

            # Thread comparison — include v2 if present
            all_results = [leg, a]
            if v2:
                all_results.append(v2)
            all_thread_names: list[str] = []
            for r in all_results:
                for t in r.profile.active_threads:
                    if t.name not in all_thread_names:
                        all_thread_names.append(t.name)

            if all_thread_names:
                thread_table = Table(title="Thread Comparison")
                thread_table.add_column("Thread", style="cyan", width=30)
                thread_table.add_column("Legacy", justify="center", width=8)
                thread_table.add_column("Agentic", justify="center", width=8)
                if has_v2:
                    thread_table.add_column("Multi-v2", justify="center", width=10)

                leg_names = {t.name for t in leg.profile.active_threads}
                a_names = {t.name for t in a.profile.active_threads}
                v2_names = {t.name for t in v2.profile.active_threads} if v2 else set()

                for name in all_thread_names:
                    row = [
                        name[:30],
                        "[yellow]yes[/yellow]" if name in leg_names else "[dim]-[/dim]",
                        "[green]yes[/green]" if name in a_names else "[dim]-[/dim]",
                    ]
                    if has_v2:
                        row.append("[magenta]yes[/magenta]" if name in v2_names else "[dim]-[/dim]")
                    thread_table.add_row(*row)
                console.print(thread_table)

            # Agentic trace summaries
            for label, result in [("Agentic", a), ("Multi-Agent v2", v2)]:
                if result is None:
                    continue
                tr = result.trace
                if tr and tr.topics_searched:
                    console.print(f"\n[bold]{label} Topics Searched:[/bold] {', '.join(tr.topics_searched)}")
                if tr and tr.tool_call_sequence:
                    console.print(f"[bold]{label} Tool Sequence:[/bold] {' -> '.join(tr.tool_call_sequence)}")

            # Save report to disk
            if save:
                from experiments.benchmarking.benchmark_report import generate_comparison_report

                report_md = generate_comparison_report(report)
                report_path = user_data_dir(user_id) / "benchmark_report.md"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report_md)
                console.print(f"\n[green]Report saved to {report_path}[/green]")

            # Trace file summary
            if trace_dir and trace_dir.exists():
                console.print(f"\n[bold]Trace Files[/bold]")
                for trace_file in sorted(trace_dir.glob("*.jsonl")):
                    line_count = sum(1 for _ in open(trace_file))
                    size_kb = trace_file.stat().st_size / 1024
                    console.print(f"  {trace_file.name}: {line_count} events, {size_kb:.1f} KB")

        finally:
            db.close()

    @cli.command("analyze-traces", hidden=True)
    @click.option("--output", "-o", type=click.Path(), help="Save report to file")
    @click.pass_context
    def analyze_traces(ctx: click.Context, output: str | None) -> None:
        """Analyze JSONL trace files from a benchmark run."""
        from experiments.benchmarking.trace_analyzer import parse_trace_dir, format_analysis_report

        user_id = ctx.obj["user"]
        trace_dir = user_data_dir(user_id) / "traces"

        if not trace_dir.exists() or not list(trace_dir.glob("trace_*.jsonl")):
            console.print("[yellow]No trace files found.[/yellow]")
            console.print(f"[dim]Run: syke benchmark --live --trace to generate traces in {trace_dir}[/dim]")
            return

        comp = parse_trace_dir(trace_dir)
        if not comp.analyses:
            console.print("[yellow]No valid trace files found.[/yellow]")
            return

        table = Table(title="Trace Analysis Summary")
        table.add_column("Method", style="cyan")
        table.add_column("Strategy", style="green")
        table.add_column("Tool Calls", justify="right")
        table.add_column("Searches", justify="right")
        table.add_column("Empty Rate", justify="right")
        table.add_column("Thinking", justify="right")
        table.add_column("Planning %", justify="right")
        table.add_column("Cost", justify="right", style="yellow")

        for row in comp.cost_quality_table():
            table.add_row(
                row["method"],
                row["strategy"],
                str(row["tool_calls"]),
                str(row["searches"]),
                f"{row['empty_rate']:.0%}",
                f"{row['thinking_chars']:,}",
                f"{row['planning_ratio']:.0%}",
                f"${row['cost_usd']:.4f}",
            )
        console.print(table)

        # Divergence
        div = comp.divergence_summary()
        console.print(f"\n[bold]Topic Divergence[/bold]")
        console.print(f"  Shared: {', '.join(div['shared_topics']) or 'none'}")
        for method, topics in div["unique_topics"].items():
            if topics:
                console.print(f"  {method} only: {', '.join(topics)}")

        # Per-method details
        for method, a in comp.analyses.items():
            console.print(f"\n[bold]{method}[/bold]")
            if a.tool_sequence:
                console.print(f"  Tools: {' -> '.join(a.tool_sequence)}")
            if a.topics_searched:
                console.print(f"  Topics: {', '.join(a.topics_searched)}")
            console.print(f"  Thinking: {len(a.thinking_blocks)} blocks, {a.total_thinking_chars:,} chars")

        # Save report
        report_md = format_analysis_report(comp)
        if output:
            Path(output).write_text(report_md)
            console.print(f"\n[green]Report saved to {output}[/green]")
        else:
            default_path = user_data_dir(user_id) / "trace_analysis.md"
            default_path.write_text(report_md)
            console.print(f"\n[green]Full report saved to {default_path}[/green]")

    @cli.command("eval", hidden=True)
    @click.option("--sources", default=None, help="Comma-separated source list for coverage scoring")
    @click.option("--freeform", is_flag=True, help="Evaluate the schema-free profile instead of UserProfile")
    @click.pass_context
    def eval_profile(ctx: click.Context, sources: str | None, freeform: bool) -> None:
        """Evaluate the latest perception profile on quality dimensions."""
        user_id = ctx.obj["user"]
        db = _get_db(user_id)
        try:
            all_sources = [s.strip() for s in sources.split(",")] if sources else db.get_sources(user_id)

            if freeform:
                from experiments.perception.eval import evaluate_freeform

                sf_path = user_data_dir(user_id) / "schema_free_profile.json"
                if not sf_path.exists():
                    console.print("[red]No schema-free profile found. Run: syke perceive --method schema-free[/red]")
                    return

                schema = json.loads(sf_path.read_text())
                result = evaluate_freeform(schema, all_sources=all_sources)
            else:
                from experiments.perception.eval import evaluate_profile

                prof = db.get_latest_profile(user_id)
                if not prof:
                    console.print("[red]No profile found. Run: syke perceive[/red]")
                    return

                result = evaluate_profile(prof, all_sources=all_sources)

            label = "Freeform" if freeform else "Profile"
            table = Table(title=f"{label} Evaluation — {user_id}")
            table.add_column("Dimension", style="cyan", width=20)
            table.add_column("Score", justify="right", style="green", width=8)
            table.add_column("Weight", justify="right", width=8)
            table.add_column("Detail", width=50)

            for d in result.dimensions:
                pct = f"{d.score * 100:.0f}%"
                table.add_row(d.name, pct, f"x{d.max_score:.2f}", d.detail)

            console.print(table)
            color = "green" if result.total_pct >= 70 else "yellow" if result.total_pct >= 50 else "red"
            console.print(f"\n[bold {color}]Composite Score: {result.total_pct:.1f}%[/bold {color}]")

        finally:
            db.close()

    @cli.command(hidden=True)
    @click.option("--sim", is_flag=True, help="Use simulation data (no DB needed)")
    @click.option("--output", "-o", type=click.Path(), help="Output HTML file path")
    @click.option("--no-open", is_flag=True, help="Don't auto-open in browser")
    @click.pass_context
    def viz(ctx: click.Context, sim: bool, output: str | None, no_open: bool) -> None:
        """Generate an interactive identity visualization."""
        from experiments.viz.viz import build_viz

        user_id = ctx.obj["user"]

        if sim:
            console.print("[cyan]Building visualization from simulation data...[/cyan]")
            path = build_viz(sim=True, output=output)
        else:
            db = _get_db(user_id)
            try:
                count = db.count_events(user_id)
                if count == 0:
                    console.print("[yellow]No events found. Run: syke setup, or use --sim for demo data.[/yellow]")
                    return
                console.print(f"[cyan]Building visualization from {count} events...[/cyan]")
                path = build_viz(db=db, user_id=user_id, output=output)
            finally:
                db.close()

        console.print(f"[green]Visualization written to {path}[/green]")

        if not no_open:
            import webbrowser
            webbrowser.open(f"file://{path}")

    @cli.command("metrics", hidden=True)
    @click.pass_context
    def show_metrics(ctx: click.Context) -> None:
        """Show cost, token, and operational metrics."""
        from syke.metrics import MetricsTracker

        user_id = ctx.obj["user"]
        tracker = MetricsTracker(user_id)
        summary = tracker.get_summary()

        console.print(f"\n[bold]Syke Metrics[/bold] — user: [cyan]{user_id}[/cyan]\n")

        if summary["total_runs"] == 0:
            console.print("[dim]No operations recorded yet.[/dim]")
            return

        table = Table(title="Overall")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right", style="green")
        table.add_row("Total runs", str(summary["total_runs"]))
        table.add_row("Total cost", f"${summary['total_cost_usd']:.4f}")
        table.add_row("Total tokens", f"{summary['total_tokens']:,}")
        table.add_row("Events processed", f"{summary['total_events_processed']:,}")
        console.print(table)

        if summary["by_operation"]:
            console.print()
            op_table = Table(title="By Operation")
            op_table.add_column("Operation", style="cyan")
            op_table.add_column("Runs", justify="right")
            op_table.add_column("Cost", justify="right", style="green")
            op_table.add_column("Tokens", justify="right")
            op_table.add_column("Errors", justify="right", style="red")

            for op, data in summary["by_operation"].items():
                op_table.add_row(
                    op,
                    str(data["count"]),
                    f"${data['cost_usd']:.4f}",
                    f"{data['total_tokens']:,}",
                    str(data["errors"]) if data["errors"] else "-",
                )
            console.print(op_table)

        if summary["last_run"]:
            last = summary["last_run"]
            console.print(f"\n[dim]Last run: {last.get('operation', '?')} at {last.get('completed_at', '?')[:19]}[/dim]")

    @cli.command(hidden=True)
    @click.pass_context
    def health(ctx: click.Context) -> None:
        """Run health checks on the Syke installation."""
        from syke.metrics import run_health_check

        user_id = ctx.obj["user"]
        results = run_health_check(user_id)

        console.print(f"\n[bold]Syke Health Check[/bold] — user: [cyan]{user_id}[/cyan]\n")

        for name, check in results["checks"].items():
            icon = "[green]OK[/green]" if check["ok"] else "[red]FAIL[/red]"
            console.print(f"  {icon}  {name:20s} {check['detail']}")

        console.print()
        if results["healthy"]:
            console.print("[bold green]All critical checks passed.[/bold green]")
        else:
            console.print("[bold red]Some critical checks failed. See above.[/bold red]")

    @cli.command(hidden=True)
    @click.option("--with-perception", is_flag=True, help="Also run incremental perception (costs money)")
    @click.pass_context
    def validate(ctx: click.Context, with_perception: bool) -> None:
        """Validate a fresh Syke setup end-to-end. Records telemetry."""
        import time as _time
        from syke.config import user_profile_path, user_data_dir
        from syke.metrics import MetricsTracker

        user_id = ctx.obj["user"]
        tracker = MetricsTracker(user_id)
        steps: list[dict] = []

        console.print(f"\n[bold]Syke Validate[/bold] — user: [cyan]{user_id}[/cyan]\n")

        def _step(name: str, fn):
            start = _time.monotonic()
            try:
                detail = fn()
                dur = (_time.monotonic() - start) * 1000
                steps.append({"name": name, "status": "pass", "duration_ms": round(dur), "detail": detail})
                console.print(f"  [green]PASS[/green]  {name:30s} {detail}")
            except Exception as e:
                dur = (_time.monotonic() - start) * 1000
                steps.append({"name": name, "status": "fail", "duration_ms": round(dur), "detail": str(e), "error": str(e)})
                console.print(f"  [red]FAIL[/red]  {name:30s} {e}")

        # 1. Environment
        def check_env():
            import sys as _sys
            assert _sys.version_info >= (3, 12), f"Python {_sys.version} < 3.12"
            return f"Python {_sys.version.split()[0]}"

        _step("environment", check_env)

        # 2. Database
        db = _get_db(user_id)
        try:
            def check_db():
                count = db.count_events(user_id)
                sources = db.get_sources(user_id)
                db.conn.execute("SELECT external_id FROM events LIMIT 1")
                return f"{count} events, {len(sources)} sources, schema migrated"

            _step("database", check_db)

            def check_sources():
                sources = db.get_sources(user_id)
                assert sources, "No sources ingested — run: syke setup"
                return f"Sources: {', '.join(sources)}"

            _step("sources", check_sources)

            def check_profile():
                prof = db.get_latest_profile(user_id)
                assert prof, "No profile — run: syke perceive"
                assert prof.identity_anchor, "Profile has empty identity_anchor"
                return f"{len(prof.active_threads)} threads, {prof.events_count} events"

            _step("profile", check_profile)

            def check_agent_tools():
                from syke.memory.tools import build_memory_mcp_server
                server = build_memory_mcp_server(db, user_id)
                tools = server.list_tools()
                tool_names = [t.name for t in tools]
                assert len(tool_names) > 0, "No memory tools registered"
                return f"{len(tool_names)} memory tools registered"

            _step("agent_tools", check_agent_tools)

            def check_gateway_push():
                from syke.ingestion.gateway import IngestGateway
                gw = IngestGateway(db, user_id)
                result = gw.push(
                    source="validate",
                    event_type="test",
                    title="Validation test event",
                    content="This event was created by syke validate to confirm the push path works.",
                    external_id="syke-validate-test",
                )
                assert result["status"] in ("ok", "duplicate"), f"Push failed: {result}"
                db.conn.execute("DELETE FROM events WHERE source = 'validate' AND user_id = ?", (user_id,))
                db.conn.commit()
                return f"Push {result['status']}, cleaned up"

            _step("gateway_push", check_gateway_push)

            def check_dist_files():
                data_dir = user_data_dir(user_id)
                claude_md = data_dir / "CLAUDE.md"
                user_md = data_dir / "USER.md"
                found = []
                if claude_md.exists():
                    found.append("CLAUDE.md")
                if user_md.exists():
                    found.append("USER.md")
                assert found, "No distribution files found — run: syke perceive"
                return ", ".join(found)

            _step("distribution_files", check_dist_files)

            if with_perception:
                def check_perception():
                    from syke.perception.agentic_perceiver import AgenticPerceiver
                    perceiver = AgenticPerceiver(db, user_id)
                    profile = perceiver.perceive(full=False)
                    return f"Perception OK, cost ${profile.cost_usd:.4f}"

                _step("perception", check_perception)

        finally:
            db.close()

        tracker.record_setup(steps)

        passed = sum(1 for s in steps if s["status"] == "pass")
        failed = sum(1 for s in steps if s["status"] == "fail")
        console.print()
        if failed == 0:
            console.print(f"[bold green]All {passed} checks passed.[/bold green]")
        else:
            console.print(f"[bold red]{failed} check(s) failed[/bold red], {passed} passed.")

    # --- Daemon commands ---

    @cli.group(hidden=True)
    def daemon() -> None:
        """Background sync daemon management."""
        pass

    @daemon.command("start")
    @click.option("--interval", default=900, help="Sync interval in seconds (default: 900)")
    @click.pass_context
    def daemon_start(ctx: click.Context, interval: int) -> None:
        """Run the daemon in foreground (Ctrl+C to stop)."""
        from syke.daemon.daemon import SykeDaemon, is_running

        user_id = ctx.obj["user"]
        running, pid = is_running()
        if running:
            console.print(f"[yellow]Daemon already running (PID {pid}). Stop it first.[/yellow]")
            return

        d = SykeDaemon(user_id, interval=interval)
        d.run()

    @daemon.command("stop")
    @click.pass_context
    def daemon_stop(ctx: click.Context) -> None:
        """Stop the running daemon."""
        from syke.daemon.daemon import stop_daemon, is_running

        running, pid = is_running()
        if not running:
            console.print("[dim]No daemon running.[/dim]")
            return

        if stop_daemon():
            console.print(f"[green]Sent SIGTERM to daemon (PID {pid}).[/green]")
        else:
            console.print("[red]Failed to stop daemon.[/red]")

    @daemon.command("status")
    @click.pass_context
    def daemon_status(ctx: click.Context) -> None:
        """Check if the daemon is running."""
        from syke.daemon.daemon import is_running, launchd_status, PLIST_PATH

        running, pid = is_running()
        if running:
            console.print(f"[green]Daemon running[/green] — PID {pid}")
        else:
            console.print("[dim]No foreground daemon running.[/dim]")

        ls = launchd_status()
        if ls:
            console.print(f"[green]LaunchAgent loaded[/green]")
            console.print(f"  {ls}")
        elif PLIST_PATH.exists():
            console.print("[yellow]LaunchAgent plist exists but not loaded.[/yellow]")
        else:
            console.print("[dim]No LaunchAgent installed.[/dim]")

    @daemon.command("install")
    @click.pass_context
    def daemon_install(ctx: click.Context) -> None:
        """Install macOS LaunchAgent for background sync."""
        from syke.daemon.daemon import install_launchd, PLIST_PATH, LOG_PATH

        user_id = ctx.obj["user"]

        if PLIST_PATH.exists():
            console.print(f"[yellow]LaunchAgent already exists at {PLIST_PATH}[/yellow]")
            if not click.confirm("Overwrite?"):
                return

        path = install_launchd(user_id)
        console.print(f"[green]LaunchAgent installed.[/green]")
        console.print(f"  Plist: {path}")
        console.print(f"  Log:   {LOG_PATH}")
        console.print(f"  Interval: every 15 minutes")
        console.print(f"\n  Uninstall with: [cyan]syke daemon uninstall[/cyan]")

    @daemon.command("uninstall")
    @click.pass_context
    def daemon_uninstall(ctx: click.Context) -> None:
        """Remove the macOS LaunchAgent."""
        from syke.daemon.daemon import uninstall_launchd

        if uninstall_launchd():
            console.print("[green]LaunchAgent uninstalled.[/green]")
        else:
            console.print("[dim]No LaunchAgent found to remove.[/dim]")

    @cli.command(hidden=True)
    @click.option("--live", is_flag=True, help="Use real Opus 4.6 for perception (~$1.50)")
    @click.pass_context
    def simulate(ctx: click.Context, live: bool) -> None:
        """Replay 14-day multi-platform simulation."""
        import json as _json
        from collections import Counter
        from experiments.simulation.simulation_data import (
            SIMULATION_TIMELINE,
            get_adapter_onboarding_order,
            get_events_by_date,
        )
        from syke.db import SykeDB
        from syke.ingestion.gateway import IngestGateway

        user_id = ctx.obj["user"]

        sim_db_path = user_data_dir(user_id) / "sim.db"
        if sim_db_path.exists():
            sim_db_path.unlink()
        db = SykeDB(sim_db_path)
        db.initialize()

        console.print("\n[bold]14-Day Federated Push Simulation[/bold]")
        console.print(f"Replaying Jan 28 -> Feb 11 across 6 platforms")
        console.print(f"Mode: [cyan]{'live (Opus 4.6)' if live else 'mock (no API cost)'}[/cyan]\n")

        gw = IngestGateway(db, user_id)
        by_date = get_events_by_date()

        date_to_new_sources: dict[str, list[str]] = {}
        for date, src in get_adapter_onboarding_order():
            date_to_new_sources.setdefault(date, []).append(src)

        total_events = 0
        total_perception_runs = 0
        sources_seen: set[str] = set()

        def _mock_profile_json(sources: list[str], count: int) -> str:
            return _json.dumps({
                "identity_anchor": f"Builder across {len(sources)} platform(s).",
                "active_threads": [
                    {"name": "Simulation", "description": "14-day replay.", "intensity": "high",
                     "platforms": sources, "recent_signals": []},
                ],
                "recent_detail": f"{count} events across {', '.join(sources)}.",
                "background_context": "Simulated profile.",
                "voice_patterns": {
                    "tone": "direct", "vocabulary_notes": [],
                    "communication_style": "technical", "examples": [],
                },
            })

        for date in sorted(by_date.keys()):
            events = by_date[date]
            result = gw.push_batch(events)
            inserted = result["inserted"]
            total_events += inserted

            source_counts = Counter(ev["source"] for ev in events)
            new_sources = date_to_new_sources.get(date, [])
            sources_seen.update(ev["source"] for ev in events)

            parts = []
            for src, cnt in sorted(source_counts.items()):
                tag = f" [yellow]<- NEW[/yellow]" if src in new_sources else ""
                parts.append(f"[green]+{cnt}[/green] {src}{tag}")
            source_line = "  ".join(parts)

            src_total = len(sources_seen)
            src_label = f"Sources: {src_total}" if src_total < 6 else "[green]All 6 platforms[/green]"
            console.print(f"[bold]{date}[/bold] | {source_line} | {src_label}")

            if inserted > 0:
                if live:
                    from syke.perception.agentic_perceiver import AgenticPerceiver

                    is_full = total_perception_runs == 0
                    perceiver = AgenticPerceiver(db, user_id)
                    profile = perceiver.perceive(full=is_full)
                    mode = "full" if is_full else "incremental"
                    console.print(
                        f"         | Sync: +{inserted} pushed -> perception ({mode}) "
                        f"| Cost: ${profile.cost_usd:.4f}"
                    )
                else:
                    from syke.models import UserProfile

                    mock_json = _mock_profile_json(sorted(sources_seen), total_events)
                    data = _json.loads(mock_json)
                    voice = data.get("voice_patterns", {})
                    profile = UserProfile(
                        user_id=user_id,
                        identity_anchor=data["identity_anchor"],
                        active_threads=data.get("active_threads", []),
                        recent_detail=data.get("recent_detail", ""),
                        background_context=data.get("background_context", ""),
                        voice_patterns={
                            "tone": voice.get("tone", ""),
                            "vocabulary_notes": voice.get("vocabulary_notes", []),
                            "communication_style": voice.get("communication_style", ""),
                            "examples": voice.get("examples", []),
                        } if voice else None,
                        sources=sorted(sources_seen),
                        events_count=total_events,
                        model="claude-opus-4-6",
                    )
                    db.save_profile(profile)
                    mode = "full" if total_perception_runs == 0 else "incremental"
                    console.print(
                        f"         | Sync: +{inserted} pushed -> perception ({mode}) | mock"
                    )

                total_perception_runs += 1

        adapter_order = " -> ".join(
            f"+{src}" for _, src in get_adapter_onboarding_order()
        )
        active_days = len(by_date)
        console.print(f"\n[bold]Summary[/bold]")
        console.print(f"  Timeline:    14 days ({active_days} active)")
        console.print(f"  Events:      {total_events} across {len(sources_seen)} platforms")
        console.print(f"  Onboarding:  {adapter_order}")
        console.print(f"  Perception:  {total_perception_runs} runs")
        console.print(f"  Cost:        $0.00 (mock)" if not live else f"  Cost:        ~$1.50 (live)")
        console.print(f"  Sim DB:      {sim_db_path}\n")

        db.close()

    @cli.command(hidden=True)
    @click.option("--runs", "-n", default=5, help="Number of meta-learning cycles (default 5)")
    @click.option("--save/--no-save", default=True, help="Save profiles to DB")
    @click.option("--record", is_flag=True, help="Record all details to data/{user}/meta_runs/")
    @click.option("--max-budget", default=15.0, help="Maximum budget in USD (default 15)")
    @click.pass_context
    def evolve(ctx: click.Context, runs: int, save: bool, record: bool, max_budget: float) -> None:
        """Run N meta-learning perception cycles, showing strategy evolution.

        Each run explores the footprint, reflects on what worked, and evolves
        the exploration strategy. Watch the spider build its web.

        Use --record to capture every thinking block, tool call, and reflection
        to data/{user}/meta_runs/ for post-run analysis.
        """
        user_id = ctx.obj["user"]

        if record:
            from experiments.perception.meta_runner import run_recorded_cycle
            output_dir = run_recorded_cycle(
                user_id=user_id,
                max_runs=runs,
                max_budget=max_budget,
                save=save,
            )
            if output_dir and output_dir != Path():
                console.print(f"\n[green]Recorded run at: {output_dir}[/green]")
            return

        from experiments.perception.meta_perceiver import MetaLearningPerceiver
        from syke.metrics import MetricsTracker

        db = _get_db(user_id)
        tracker = MetricsTracker(user_id)

        try:
            events_count = db.count_events(user_id)
            sources = db.get_sources(user_id)
            if events_count == 0:
                console.print("[yellow]No events found. Run: syke setup first.[/yellow]")
                return

            console.print(f"\n[bold]Meta-Learning Evolution[/bold] — user: [cyan]{user_id}[/cyan]")
            console.print(f"  Runs: {runs}")
            console.print(f"  Events: {events_count} across {len(sources)} sources")
            console.print(f"  Spider building its web...\n")

            def on_discovery(event_type: str, detail: str) -> None:
                if event_type == "meta_cycle":
                    console.print(f"\n[bold yellow]{detail}[/bold yellow]")
                elif event_type == "tool_call":
                    console.print(f"  [cyan]>[/cyan] {detail}")
                elif event_type == "result":
                    console.print(f"  [green]{detail}[/green]")
                elif event_type == "reflection":
                    console.print(f"  [magenta]REFLECT:[/magenta] {detail}")
                elif event_type == "evolution":
                    console.print(f"  [bold yellow]EVOLVE:[/bold yellow] {detail}")
                elif event_type == "hook_gate":
                    console.print(f"  [red]GATE:[/red] {detail}")
                elif event_type == "hook_correction":
                    console.print(f"  [yellow]CORRECTED:[/yellow] {detail}")

            with tracker.track("evolve_meta", mode="full") as metrics:
                perceiver = MetaLearningPerceiver(db, user_id)
                results = perceiver.run_cycle(
                    n_runs=runs, on_discovery=on_discovery, save=save,
                    max_budget_usd=max_budget,
                )
                total_cost = sum(r.metrics.cost_usd for r in results)
                metrics.cost_usd = total_cost
                metrics.events_processed = events_count
                metrics.method = "meta"
                metrics.num_turns = sum(r.metrics.num_turns for r in results)

            # Summary table with efficiency columns
            summary_table = Table(title="Meta-Learning Evolution Summary")
            summary_table.add_column("Run", style="cyan", justify="right", width=5)
            summary_table.add_column("Score", justify="right", style="green", width=8)
            summary_table.add_column("Cost", justify="right", style="yellow", width=10)
            summary_table.add_column("Eff.", justify="right", style="bold green", width=8)
            summary_table.add_column("Search%", justify="right", style="bold cyan", width=8)
            summary_table.add_column("Useful", justify="right", width=8)
            summary_table.add_column("Wasted", justify="right", width=8)
            summary_table.add_column("Connections", justify="right", width=12)
            summary_table.add_column("Strategy", justify="right", width=10)

            for i, r in enumerate(results):
                t = r.trace
                # Cost efficiency: score per dollar
                eff = t.profile_score / t.cost_usd if t.cost_usd > 0 else 0.0
                # Search efficiency: useful / (useful + wasted)
                total_searches = len(t.useful_searches) + len(t.wasted_searches)
                search_pct = len(t.useful_searches) / total_searches if total_searches > 0 else 0.0
                summary_table.add_row(
                    str(i + 1),
                    f"{t.profile_score:.0%}",
                    f"${t.cost_usd:.4f}",
                    f"{eff:.1f}",
                    f"{search_pct:.0%}",
                    str(len(t.useful_searches)),
                    str(len(t.wasted_searches)),
                    str(len(t.discovered_connections)),
                    f"v{r.strategy_version}",
                )

            console.print(summary_table)

            # Cost & search efficiency trajectory
            if len(results) >= 2:
                first_t, last_t = results[0].trace, results[-1].trace
                # Cost efficiency
                eff_first = first_t.profile_score / first_t.cost_usd if first_t.cost_usd > 0 else 0.0
                eff_last = last_t.profile_score / last_t.cost_usd if last_t.cost_usd > 0 else 0.0
                if eff_first > 0:
                    eff_delta = ((eff_last - eff_first) / eff_first) * 100
                    eff_color = "green" if eff_delta > 0 else "red"
                    console.print(f"\n  [{eff_color}]Cost efficiency: {eff_delta:+.0f}% from run 1 to run {len(results)}[/{eff_color}]")

                # Search efficiency
                def _search_pct(t):
                    total = len(t.useful_searches) + len(t.wasted_searches)
                    return len(t.useful_searches) / total if total > 0 else 0.0

                sp_first, sp_last = _search_pct(first_t), _search_pct(last_t)
                if sp_first > 0:
                    sp_delta = ((sp_last - sp_first) / sp_first) * 100
                    sp_color = "green" if sp_delta > 0 else "red"
                    console.print(f"  [{sp_color}]Search efficiency: {sp_delta:+.0f}% from run 1 to run {len(results)}[/{sp_color}]")

                # Dead ends avoided
                all_dead = set()
                for r in results:
                    all_dead.update(r.trace.wasted_searches)
                if all_dead:
                    console.print(f"  Dead ends avoided: {len(all_dead)} searches the agent learned to skip")

            # Score trajectory
            scores = [r.trace.profile_score for r in results]
            if len(scores) >= 2:
                delta = scores[-1] - scores[0]
                direction = "[green]improved[/green]" if delta > 0 else "[red]declined[/red]" if delta < 0 else "[dim]unchanged[/dim]"
                console.print(f"\n  Score trajectory: {' -> '.join(f'{s:.0%}' for s in scores)}")
                console.print(f"  Overall: {direction} by {abs(delta):.0%}")

            # --- What ALMA Discovered (profile diff) ---
            if len(results) >= 2:
                first_profile = results[0].profile
                last_profile = results[-1].profile

                console.print(f"\n[bold]What ALMA Discovered[/bold]")

                # New threads
                first_thread_names = {
                    t.name if hasattr(t, "name") else str(t)
                    for t in first_profile.active_threads
                }
                last_thread_names = {
                    t.name if hasattr(t, "name") else str(t)
                    for t in last_profile.active_threads
                }
                new_threads = last_thread_names - first_thread_names
                if new_threads:
                    console.print(f"  New threads: [green]{', '.join(sorted(new_threads))}[/green]")

                # Identity anchor growth
                first_anchor_len = len(first_profile.identity_anchor or "")
                last_anchor_len = len(last_profile.identity_anchor or "")
                anchor_delta = last_anchor_len - first_anchor_len
                # Count proper nouns in last anchor
                last_anchor = last_profile.identity_anchor or ""
                import re as _re
                proper_nouns = [
                    w for w in last_anchor.split()
                    if w[0:1].isupper() and len(w) > 2
                    and w not in ("The", "This", "That", "His", "Her", "And", "But", "Not")
                ]
                first_anchor = first_profile.identity_anchor or ""
                first_nouns = {
                    w for w in first_anchor.split()
                    if w[0:1].isupper() and len(w) > 2
                    and w not in ("The", "This", "That", "His", "Her", "And", "But", "Not")
                }
                new_nouns = [w for w in proper_nouns if w not in first_nouns]
                if anchor_delta != 0 or new_nouns:
                    parts = []
                    if anchor_delta != 0:
                        parts.append(f"{anchor_delta:+d} chars")
                    if new_nouns:
                        parts.append(f"{len(new_nouns)} new proper nouns")
                    console.print(f"  Identity anchor: {', '.join(parts)}")

                # Cross-platform connections from strategy
                strategy = perceiver.archive.get_latest_strategy()
                cross_plat_count = len(strategy.cross_platform_topics) if strategy else 0

                # Summary line
                first_platforms = set()
                for t in first_profile.active_threads:
                    first_platforms.update(getattr(t, "platforms", []) or [])
                last_platforms = set()
                for t in last_profile.active_threads:
                    last_platforms.update(getattr(t, "platforms", []) or [])

                console.print(
                    f"  Run 1 saw {len(first_thread_names)} threads across {len(first_platforms)} platforms. "
                    f"Run {len(results)} found {len(last_thread_names)} threads across "
                    f"{len(last_platforms)} platforms"
                    + (f", including {cross_plat_count} cross-platform connections." if cross_plat_count else ".")
                )

            # Strategy status
            strategy = perceiver.archive.get_latest_strategy()
            if strategy:
                console.print(f"\n  Strategy v{strategy.version}:")
                if strategy.productive_searches:
                    queries = [ps.query for ps in strategy.productive_searches[:5]]
                    console.print(f"    Productive: {', '.join(queries)}")
                if strategy.dead_end_searches:
                    console.print(f"    Dead ends: {', '.join(strategy.dead_end_searches[:5])}")
                if strategy.cross_platform_topics:
                    topics = [ct.topic for ct in strategy.cross_platform_topics[:3]]
                    console.print(f"    Cross-platform: {', '.join(topics)}")

            # --- Convergence indicator ---
            if len(results) >= 3:
                # Check which productive searches are stable across the last 3 runs
                last_3 = results[-3:]
                search_appearances: dict[str, int] = {}
                for r in last_3:
                    for s in r.trace.useful_searches:
                        search_appearances[s] = search_appearances.get(s, 0) + 1

                stable = [s for s, count in search_appearances.items() if count >= 3]
                total_productive = len(search_appearances) if search_appearances else 1
                convergence_pct = len(stable) / total_productive * 100

                console.print(f"\n  Strategy convergence: {convergence_pct:.0f}% of productive searches stable across last 3 runs")
                if convergence_pct >= 70:
                    console.print("  [green]Strategy has converged — the web is dense.[/green]")
                else:
                    remaining = max(1, 3 - len(results))
                    console.print(f"  [yellow]Strategy still exploring — {remaining}+ more runs recommended.[/yellow]")

            console.print(f"\n  Total cost: ${total_cost:.4f}")
            console.print(f"  Archive: {perceiver.archive.run_count} traces")

        finally:
            db.close()

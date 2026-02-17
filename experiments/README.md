# Experiments

Untracked experiment code. Organized, useful, not shipped.

## Structure

- `perception/` — Schema-free perceiver, eval framework, ALMA evolution
- `benchmarking/` — Benchmark runner, trace analysis, reports
- `simulation/` — Simulate command, fixture data
- `viz/` — HTML visualizer
- `daemon/` — Background sync daemon
- `stubs/` — Platform adapter stubs (twitter, youtube)

## Usage

These modules import from the main `syke` package and work as long as you're in the repo root with the venv activated. Experiment CLI commands are auto-registered in `cli_experiments.py` when available.

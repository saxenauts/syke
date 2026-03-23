# Experiments

Local development and evaluation material for the 0.5 branch. Experimental, repo-local, and not part of the stable product surface.

## Scope

This directory is where synthesis, replay, and evaluation ideas are tested against the current system. These files may be tracked in git, but they should still be read as working material rather than product guarantees.

## Structure

- `benchmarking/` — Benchmark runner, trace analysis, reports
- `simulation/` — Simulate command, fixture data
- `viz/` — HTML visualizer

## Usage

These modules import from the main `syke` package and work as long as you're in the repo root with the venv activated.

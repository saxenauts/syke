FROM python:3.14-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Create isolated workspace — no .claude/, no project files
WORKDIR /workspace

# Install syke + claude-agent-sdk (bundles the Claude CLI binary)
COPY pyproject.toml uv.lock ./
COPY syke/ ./syke/
RUN pip install --no-cache-dir -e ".[all]" 2>/dev/null || pip install --no-cache-dir -e .

# Copy only the replay script and sandbox runner
COPY experiments/memory_replay.py ./replay.py
COPY experiments/sandbox/run.py ./run.py

# No .claude/ directory exists in this image
# No project memory, no CLAUDE.md, no commands
# The agent sees only what we mount and inject

# Mount points:
#   /data/frozen.db   — frozen dataset (read-only)
#   /output           — results, memex versions, replay.db (read-write)
#   /skill.md         — prompt file (read-only, optional)

VOLUME ["/data", "/output"]

ENTRYPOINT ["python", "run.py"]

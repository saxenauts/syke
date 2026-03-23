from __future__ import annotations

import json
from pathlib import Path
from typing import cast


READ_CHUNK_SIZE = 64 * 1024
JsonRecord = dict[str, object]


class JsonlTailer:
    def __init__(self, file_path: Path, *, suppress_history: bool = False):
        self.file_path: Path = file_path
        self._suppress_history: bool = suppress_history
        self._offset: int = 0
        self._inode: int | None = None
        self._buffer: bytes = b""
        self._failures: list[str] = []

    def poll(self) -> list[JsonRecord]:
        if not self.file_path.exists():
            return []

        stat = self.file_path.stat()
        inode = stat.st_ino

        if self._inode is None and self._suppress_history:
            self._inode = inode
            self._offset = stat.st_size
            self._buffer = b""
            return []

        if self._inode is None:
            self._inode = inode
        elif inode != self._inode:
            self._inode = inode
            self._offset = 0
            self._buffer = b""
        elif stat.st_size < self._offset:
            self._offset = 0
            self._buffer = b""

        records: list[JsonRecord] = []
        self._failures = []
        with self.file_path.open("rb") as handle:
            _ = handle.seek(self._offset)
            pending = self._buffer

            while True:
                chunk = handle.read(READ_CHUNK_SIZE)
                if not chunk:
                    break

                pending += chunk
                parts = pending.split(b"\n")
                complete_lines = parts[:-1]
                pending = parts[-1]

                for line in complete_lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decoded = line.decode("utf-8")
                        parsed = cast(object, json.loads(decoded))
                        if isinstance(parsed, dict):
                            records.append(cast(JsonRecord, parsed))
                    except UnicodeDecodeError:
                        self._failures.append(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        try:
                            decoded = line.decode("utf-8")
                            self._failures.append(decoded)
                        except UnicodeDecodeError:
                            self._failures.append(line.decode("utf-8", errors="replace"))

            self._buffer = pending
            self._offset = handle.tell()

        return records

    def get_failures(self) -> list[str]:
        """Return list of raw lines that failed to parse since last poll."""
        return list(self._failures)

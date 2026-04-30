# antigravity

Antigravity is a browser-based AI agent from Google that performs web tasks, research, and code-related workflows. It produces structured workflow artifacts: task definitions, implementation plans, walkthroughs, browser recording metadata, and screenshot verification metadata. These are stored as JSON, JSONL, markdown, and YAML files in a local directory hierarchy.

## Where

```
~/.antigravity/**/*
~/.gemini/antigravity/**/*
~/.gemini/**/*
```

Catalog-specified discovery patterns:
```
~/.gemini/antigravity/brain/**/*.md
~/.gemini/antigravity/brain/**/*.md.metadata.json
~/.gemini/antigravity/browser_recordings/*/metadata.json
```

The adapter accepts files with these extensions: `.json`, `.jsonl`, `.md`, `.markdown`, `.yaml`, `.yml`, `.txt`.

Directories that are skipped during discovery (noise directories): `.git`, `cache`, `caches`, `extensions`, `logs`, `node_modules`, `tmp`, `temp`, `antigravity-browser-profile`.

## Sessions

Sessions are not file-level. Multiple files are grouped into a single session based on directory structure. The grouping key is the parent directory, with an extra level of unwrapping for directories named: `artifacts`, `artifact`, `workflow`, `workflows`, `session`, `sessions`, `run`, `runs`, `task`, `tasks` (in these cases, the grandparent is used as the group key).

All candidate files sharing the same group key are assembled into one session. The session ID is the group key directory name (unless it matches a source root name, in which case the first file's stem is used).

Recency is determined by the newest `st_mtime` across all files in the group.

## Format

Mixed: JSON, JSONL, markdown, YAML, plain text. Each file is classified into an artifact family based on its filename.

### Artifact family classification

The adapter classifies files by filename patterns:

| Pattern in filename | Family | Role |
|---|---|---|
| `task`, or `/task` in path | `task` | user |
| `implementation-plan`, `implementation_plan`, `implementationplan`, `plan` | `implementation_plan` | assistant |
| `walkthrough` | `walkthrough` | assistant |
| `recording`, `browser` | `browser_recording_metadata` | assistant |
| `verification`, `verify`, `screenshot` | `screenshot_verification_metadata` | assistant |

Files that do not match any pattern are excluded. The `task` family produces user-role turns; all others produce assistant-role turns.

### JSON / JSONL files

For JSON files containing a list, each item produces a separate turn. For JSON files containing an object, the object produces one turn.

Content is extracted from these keys (in order): `title`, `name`, `summary`, `description`, `task`, `prompt`, `goal`, `plan`, `walkthrough`, `verdict`, `result`, `status`, `notes`, `content`, `markdown`, `text`. Keys like `title`, `name`, etc. are formatted as `"key: value"`. Keys `content`, `markdown`, `text` are used as-is.

Project is extracted from: `project`, `workspace`, `cwd`, `path`, `root`.

Timestamps are extracted from: `timestamp`, `createdAt`, `updatedAt`, `completedAt`, `recordedAt`, `verifiedAt`, `startTime`, `endTime`.

### Markdown / text files

The entire file content is read as a single turn. The timestamp is the file's modification time. No structured field extraction is performed.

### Message types / Turn structure

There are no traditional multi-turn conversations. Each file produces one or more turns with role determined by the artifact family. Turns from all files in a group are sorted by timestamp, then role, then content.

### What to ignore

- Files in noise directories (listed above)
- Files with extensions outside the accepted set
- Files whose name does not match any artifact family pattern
- Empty files
- JSON objects where no content keys produce text

### Metadata

Per turn: `artifact_family`, `artifact_path`, `source_line_index` (JSONL), `source_index` (JSON list), `source_event_type`, `source_extension`, `status`.

Per session: `artifact_families` (sorted unique list), `source_root`, `artifact_count`.

## What sessions contain

Sessions represent completed or in-progress workflows. A typical session contains: a task definition (what the user asked for), an implementation plan (how the agent will accomplish it), a walkthrough of the execution, browser recording metadata (URLs visited, actions taken), and screenshot verification metadata (visual verification of results). Not all artifact types are present in every session.

## Harness memory

Antigravity stores brain/knowledge files under `~/.gemini/antigravity/brain/` as markdown files with accompanying `.md.metadata.json` sidecar files. These contain accumulated knowledge from previous workflows.

## Distribution

To write context back to Antigravity:

- Write markdown files to `~/.gemini/antigravity/brain/` with accompanying `.md.metadata.json` sidecar files
- Brain files are discovered and used by Antigravity as knowledge context for subsequent workflows

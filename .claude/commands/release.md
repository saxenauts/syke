---
name: release
description: Release a new version of Syke to PyPI. Takes <version> "<codename>" as args.
user_invocable: true
---

# Release $ARGUMENTS

You are releasing Syke version $ARGUMENTS. Parse the arguments as `<version> "<codename>"` (e.g., `0.2.0 "The Agent Remembers"`).

Follow these steps exactly. Stop and abort if any step fails.

## Step 1: Run tests

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

If any tests fail, STOP. Fix them first, do not proceed with the release.

## Step 2: Get git log since last tag

```bash
git log --oneline $(git describe --tags --abbrev=0 2>/dev/null || git rev-list --max-parents=0 HEAD)..HEAD
```

This shows all commits that will be included in this release.

## Step 3: Draft CHANGELOG entry

Using the git log and your knowledge of what changed, draft a CHANGELOG.md entry in this format:

```
## [<version>] — <YYYY-MM-DD> — "<codename>"

<One-line summary of the release theme.>

- Bullet point for each notable change
- Group related changes
- Keep it concise
```

## Step 4: Get approval

Show the draft CHANGELOG entry to the user. Ask if they want to approve it as-is or make edits. Do NOT proceed until approved.

## Step 5: Update CHANGELOG.md

Prepend the approved entry to CHANGELOG.md, after the `# Changelog` header and the "All notable changes..." line. Keep existing entries intact.

## Step 5b: Sync docs-site changelog

The docs website has its own changelog at `docs-site/pages/changelog.mdx`.
Prepend the same entry there (after the `# Changelog` header line), then stage:

```bash
git add docs-site/pages/changelog.mdx
```

## Step 6: Stage CHANGELOG.md

```bash
git add CHANGELOG.md
```

## Step 7: Bump version locally (no push)

```bash
tbump <version> --non-interactive --no-push
```

This will:
- Update version in `pyproject.toml` and `syke/__init__.py`
- Stage and commit the CHANGELOG + version bump
- Create a git tag `v<version>`
- **NOT push** — we validate first

## Step 8: Build and validate the package

```bash
source .venv/bin/activate && python -m build && twine check dist/syke-<version>*
```

If the build or twine check fails, undo the release:
```bash
git tag -d v<version> && git reset --soft HEAD~1 && git restore --staged .
```
Then STOP and fix the issue.

## Step 9: Push and ship

Everything validated locally. Now push:

```bash
git push origin main && git push origin v<version>
```

If the push fails (e.g. rejected), STOP. Do NOT force push. The local commit and tag are still safe — fix the issue and retry.

## Step 10: Done

Print:

> Released v<version>. GitHub Actions will publish to PyPI and create a GitHub Release.
> Watch: https://github.com/saxenauts/syke/actions
>
> If CI fails, you can safely roll back:
> ```
> git push origin :refs/tags/v<version>   # delete remote tag
> git tag -d v<version>                    # delete local tag
> git revert HEAD                          # revert the version commit
> ```

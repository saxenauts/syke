# Releasing Syke

## Pre-release checklist

1. All tests pass: `python -m pytest tests/ --ignore=tests/test_install_surface.py -x`
2. Ruff clean: `ruff check && ruff format --check`
3. Version bumped in `pyproject.toml` via `tbump`
4. CHANGELOG.md updated with new version section
5. Fresh install test: `bash scripts/fresh-install-test.sh`
6. Smoke test: `bash scripts/release-preflight.sh`

## Release process

```bash
# 1. Bump version
tbump <new-version>

# 2. Push tag
git push origin main --tags

# 3. CI publishes to PyPI via .github/workflows/publish.yml
```

## Post-release

- Verify `pip install syke` installs the new version
- Run `syke --version` to confirm
- Test fresh setup: `syke setup`

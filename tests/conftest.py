"""Test fixtures and sample data."""

from __future__ import annotations

import pytest

from syke.db import SykeDB


@pytest.fixture
def db(tmp_path):
    """Create a temporary in-memory database."""
    with SykeDB(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def user_id():
    return "test_user"

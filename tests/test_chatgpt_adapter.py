"""Tests for the ChatGPT adapter."""

import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from syke.db import SykeDB
from syke.ingestion.chatgpt import ChatGPTAdapter


@pytest.fixture
def sample_export(tmp_path):
    """Create a sample ChatGPT export ZIP."""
    conversations = [
        {
            "id": "conv1",
            "title": "Python help",
            "create_time": 1706000000.0,
            "update_time": 1706001000.0,
            "default_model_slug": "gpt-4",
            "mapping": {
                "node1": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["How do I sort a list in Python?"]},
                    }
                },
                "node2": {
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["You can use the sorted() function or list.sort() method."]},
                    }
                },
            },
        },
        {
            "id": "conv2",
            "title": "Recipe ideas",
            "create_time": 1706100000.0,
            "update_time": 1706101000.0,
            "default_model_slug": "gpt-4",
            "mapping": {
                "node1": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["Give me a simple pasta recipe."]},
                    }
                },
                "node2": {
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["Here's a simple aglio e olio recipe..."]},
                    }
                },
            },
        },
    ]

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conversations))

    return zip_path


def test_chatgpt_ingestion(db, user_id, sample_export):
    """ChatGPT adapter parses export and creates events."""
    adapter = ChatGPTAdapter(db, user_id)
    result = adapter.ingest(file_path=str(sample_export))

    assert result.events_count == 2
    assert result.source == "chatgpt"

    events = db.get_events(user_id, source="chatgpt")
    assert len(events) == 2

    titles = {e["title"] for e in events}
    assert "Python help" in titles
    assert "Recipe ideas" in titles


def test_chatgpt_missing_file(db, user_id, tmp_path):
    """ChatGPT adapter raises on missing conversations.json."""
    zip_path = tmp_path / "empty.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "nothing here")

    adapter = ChatGPTAdapter(db, user_id)
    with pytest.raises(ValueError, match="No conversations.json"):
        adapter.ingest(file_path=str(zip_path))

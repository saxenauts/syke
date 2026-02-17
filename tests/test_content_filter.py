"""Tests for the ContentFilter class in syke/ingestion/base.py."""

from syke.ingestion.base import ContentFilter


# --- Helpers ---

def _make_whatsapp_line(index: int = 0) -> str:
    """Generate a WhatsApp-format message line."""
    return f"[10/6/25, 5:08:3{index % 10} AM] Alice: Hey, are you coming tonight?"


def _make_normal_lines(count: int) -> list[str]:
    """Generate normal technical content lines."""
    return [f"Line {i}: Implementing the data pipeline for event ingestion." for i in range(count)]


# --- Credential stripping ---

class TestCredentialStripping:
    """Each credential pattern should be replaced with [REDACTED]."""

    def test_anthropic_key(self):
        cf = ContentFilter()
        content = "My key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "sk-ant-api03" not in result

    def test_openai_key(self):
        cf = ContentFilter()
        content = "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result

    def test_github_personal_access_token(self):
        cf = ContentFilter()
        content = "token: ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "ghp_" not in result

    def test_github_oauth_token(self):
        cf = ContentFilter()
        content = "oauth: gho_abcdefghijklmnopqrstuvwxyz1234567890AB"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "gho_" not in result

    def test_slack_token(self):
        cf = ContentFilter()
        content = "SLACK_TOKEN=xoxb-123456789-abcdef"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "xoxb-" not in result

    def test_bearer_token(self):
        cf = ContentFilter()
        content = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "eyJhbGciOiJ" not in result

    def test_password_equals(self):
        cf = ContentFilter()
        content = "password = mysecretpass123"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "mysecretpass123" not in result

    def test_password_colon(self):
        cf = ContentFilter()
        content = 'PASSWORD: "supersecure99"'
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "supersecure99" not in result

    def test_aws_access_key(self):
        cf = ContentFilter()
        content = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_ssh_private_key(self):
        cf = ContentFilter()
        content = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "BEGIN RSA PRIVATE KEY" not in result

    def test_ec_private_key(self):
        cf = ContentFilter()
        content = "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEE..."
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "BEGIN EC PRIVATE KEY" not in result

    def test_db_connection_string(self):
        cf = ContentFilter()
        content = "DATABASE_URL=postgres://admin:s3cretP4ss@db.example.com:5432/mydb"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "s3cretP4ss" not in result

    def test_mysql_connection_string(self):
        cf = ContentFilter()
        content = "mysql://root:hunter2@localhost/production"
        result = cf.sanitize(content)
        assert "[REDACTED]" in result
        assert "hunter2" not in result

    def test_multiple_credentials_in_one_content(self):
        cf = ContentFilter()
        content = (
            "OPENAI_KEY=sk-abcdefghijklmnopqrstuvwxyz1234567890\n"
            "AWS_KEY=AKIAIOSFODNN7EXAMPLE\n"
            "DB=postgres://user:pass123@host/db"
        )
        result = cf.sanitize(content)
        assert result.count("[REDACTED]") >= 3

    def test_sanitize_increments_sanitized_stat(self):
        cf = ContentFilter()
        content = "key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
        cf.sanitize(content)
        assert cf.stats["sanitized"] == 1

    def test_no_credentials_does_not_increment_sanitized(self):
        cf = ContentFilter()
        content = "Just normal text about Python and data pipelines."
        cf.sanitize(content)
        assert cf.stats["sanitized"] == 0


# --- Private message detection ---

class TestPrivateMessageDetection:
    """WhatsApp/iMessage/Telegram format lines should trigger skip."""

    def test_whatsapp_above_threshold_skips(self):
        """Content with >20% WhatsApp lines should be skipped."""
        cf = ContentFilter()
        # 6 normal lines + 4 WhatsApp lines = 10 lines total, 40% match
        normal = _make_normal_lines(6)
        whatsapp = [_make_whatsapp_line(i) for i in range(4)]
        content = "\n".join(normal + whatsapp)
        skip, reason = cf.should_skip(content)
        assert skip is True
        assert "private messaging" in reason

    def test_whatsapp_below_threshold_keeps(self):
        """Content with <=20% WhatsApp lines should not be skipped."""
        cf = ContentFilter()
        # 40 normal lines + 1 WhatsApp line = 41 lines, ~2.4% match
        normal = _make_normal_lines(40)
        whatsapp = [_make_whatsapp_line(0)]
        content = "\n".join(normal + whatsapp)
        skip, reason = cf.should_skip(content)
        assert skip is False

    def test_exactly_at_20_percent_boundary_keeps(self):
        """Content with exactly 20% WhatsApp lines should NOT be skipped (> not >=)."""
        cf = ContentFilter()
        # 8 normal lines + 2 WhatsApp lines = 10 lines, exactly 20%
        normal = _make_normal_lines(8)
        whatsapp = [_make_whatsapp_line(i) for i in range(2)]
        content = "\n".join(normal + whatsapp)
        skip, reason = cf.should_skip(content)
        assert skip is False

    def test_above_10_absolute_lines_skips_regardless_of_ratio(self):
        """More than 10 private message lines triggers skip even with low ratio."""
        cf = ContentFilter()
        # 200 normal lines + 11 WhatsApp lines = 211 lines, ~5.2% ratio
        # But >10 absolute count triggers the second threshold
        normal = _make_normal_lines(200)
        whatsapp = [_make_whatsapp_line(i) for i in range(11)]
        content = "\n".join(normal + whatsapp)
        skip, reason = cf.should_skip(content)
        assert skip is True
        assert "embedded private messages" in reason

    def test_exactly_10_absolute_lines_keeps(self):
        """Exactly 10 private message lines should NOT be skipped (> not >=)."""
        cf = ContentFilter()
        # 200 normal lines + 10 WhatsApp lines = 210, ~4.8% ratio, exactly 10 count
        normal = _make_normal_lines(200)
        whatsapp = [_make_whatsapp_line(i) for i in range(10)]
        content = "\n".join(normal + whatsapp)
        skip, reason = cf.should_skip(content)
        assert skip is False

    def test_short_content_with_messages_not_skipped(self):
        """Content with <=5 lines is not checked for private messages."""
        cf = ContentFilter()
        # 5 lines total, all WhatsApp, but <=5 lines means the check is skipped
        whatsapp = [_make_whatsapp_line(i) for i in range(5)]
        content = "\n".join(whatsapp)
        skip, reason = cf.should_skip(content)
        assert skip is False

    def test_skipped_increments_stat(self):
        """Skipping for private messages increments the skipped stat."""
        cf = ContentFilter()
        normal = _make_normal_lines(2)
        whatsapp = [_make_whatsapp_line(i) for i in range(10)]
        content = "\n".join(normal + whatsapp)
        cf.should_skip(content)
        assert cf.stats["skipped"] == 1


# --- Empty content ---

class TestEmptyContent:
    """Empty content should be skipped."""

    def test_empty_string_skips(self):
        cf = ContentFilter()
        skip, reason = cf.should_skip("")
        assert skip is True
        assert "empty" in reason

    def test_none_like_empty_skips(self):
        """Falsy empty string should be skipped."""
        cf = ContentFilter()
        skip, reason = cf.should_skip("")
        assert skip is True


# --- Normal content passthrough ---

class TestNormalContent:
    """Technical content, code, etc. should pass through unchanged."""

    def test_technical_content_unchanged(self):
        cf = ContentFilter()
        content = "Implementing a SQLite adapter for timeline storage using Python 3.12."
        result = cf.sanitize(content)
        assert result == content

    def test_code_content_unchanged(self):
        cf = ContentFilter()
        content = (
            "def process_events(events: list[Event]) -> int:\n"
            "    count = 0\n"
            "    for event in events:\n"
            "        db.insert(event)\n"
            "        count += 1\n"
            "    return count\n"
        )
        result = cf.sanitize(content)
        assert result == content

    def test_multiline_technical_unchanged(self):
        cf = ContentFilter()
        content = (
            "Architecture notes:\n"
            "- Ingestion layer reads from platform adapters\n"
            "- Events stored in SQLite with dedup\n"
            "- Perception runs Opus 4.6 with extended thinking\n"
            "- Distribution via MCP server or file injection\n"
        )
        result = cf.sanitize(content)
        assert result == content

    def test_normal_content_not_skipped(self):
        cf = ContentFilter()
        content = "\n".join(_make_normal_lines(20))
        skip, reason = cf.should_skip(content)
        assert skip is False
        assert reason == ""


# --- Stats tracking ---

class TestStats:
    """Verify stats dict is updated correctly."""

    def test_initial_stats(self):
        cf = ContentFilter()
        assert cf.stats == {"kept": 0, "skipped": 0, "sanitized": 0}

    def test_kept_incremented_on_sanitize(self):
        cf = ContentFilter()
        cf.sanitize("Normal text.")
        assert cf.stats["kept"] == 1

    def test_sanitized_incremented_when_credentials_found(self):
        cf = ContentFilter()
        cf.sanitize("key = sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert cf.stats["sanitized"] == 1
        assert cf.stats["kept"] == 1

    def test_skipped_incremented_on_private_messages(self):
        cf = ContentFilter()
        normal = _make_normal_lines(2)
        whatsapp = [_make_whatsapp_line(i) for i in range(10)]
        content = "\n".join(normal + whatsapp)
        cf.should_skip(content)
        assert cf.stats["skipped"] == 1

    def test_stats_accumulate_across_calls(self):
        cf = ContentFilter()
        cf.sanitize("Normal content one.")
        cf.sanitize("Normal content two.")
        cf.sanitize("key: sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert cf.stats["kept"] == 3
        assert cf.stats["sanitized"] == 1

    def test_empty_skip_does_not_increment_skipped_stat(self):
        """Empty content skips but does NOT increment the skipped counter
        (only private message skips do)."""
        cf = ContentFilter()
        cf.should_skip("")
        assert cf.stats["skipped"] == 0


# --- process() method (full pipeline) ---

class TestProcess:
    """Test the full filter pipeline."""

    def test_empty_content_returns_none(self):
        cf = ContentFilter()
        result, reason = cf.process("")
        assert result is None
        assert "empty" in reason

    def test_private_messages_returns_none(self):
        cf = ContentFilter()
        normal = _make_normal_lines(2)
        whatsapp = [_make_whatsapp_line(i) for i in range(10)]
        content = "\n".join(normal + whatsapp)
        result, reason = cf.process(content)
        assert result is None
        assert "private" in reason

    def test_normal_content_returns_content(self):
        cf = ContentFilter()
        content = "Working on the ingestion pipeline today."
        result, reason = cf.process(content)
        assert result == content
        assert reason == "kept"

    def test_credential_content_returns_sanitized(self):
        cf = ContentFilter()
        content = "Use this key: sk-abcdefghijklmnopqrstuvwxyz1234567890 to authenticate."
        result, reason = cf.process(content)
        assert result is not None
        assert "[REDACTED]" in result
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result
        assert reason == "kept"

    def test_process_updates_all_stats(self):
        cf = ContentFilter()
        # One kept
        cf.process("Normal text.")
        # One sanitized
        cf.process("key = sk-abcdefghijklmnopqrstuvwxyz1234567890")
        # One skipped
        cf.process("")

        assert cf.stats["kept"] == 2
        assert cf.stats["sanitized"] == 1
        # Empty skip does not increment skipped stat
        assert cf.stats["skipped"] == 0

    def test_process_with_title(self):
        cf = ContentFilter()
        result, reason = cf.process("Some technical content.", title="My Session")
        assert result == "Some technical content."
        assert reason == "kept"

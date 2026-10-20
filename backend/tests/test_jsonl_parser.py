"""Tests for JSONL transcript parser."""

from pathlib import Path

from app.core.jsonl_parser import get_first_user_prompt, get_last_assistant_response


class TestGetLastAssistantResponse:
    """Tests for get_last_assistant_response function."""

    def test_returns_last_text_response(self, sample_jsonl_file: Path) -> None:
        """Should return the last assistant text response."""
        result = get_last_assistant_response(sample_jsonl_file)
        assert result == "Final response with text"

    def test_nonexistent_file_returns_none(self, temp_dir: Path) -> None:
        """Nonexistent file should return None."""
        result = get_last_assistant_response(temp_dir / "nonexistent.jsonl")
        assert result is None

    def test_empty_file_returns_none(self, temp_dir: Path) -> None:
        """Empty file should return None."""
        empty_file = temp_dir / "empty.jsonl"
        empty_file.write_text("", encoding="utf-8")
        result = get_last_assistant_response(empty_file)
        assert result is None

    def test_no_assistant_messages_returns_none(self, temp_dir: Path) -> None:
        """File with no assistant messages should return None."""
        jsonl_path = temp_dir / "user_only.jsonl"
        jsonl_path.write_text(
            '{"type": "user", "message": {"role": "user", "content": "Hello"}}\n',
            encoding="utf-8",
        )
        result = get_last_assistant_response(jsonl_path)
        assert result is None

    def test_malformed_json_skipped(self, temp_dir: Path) -> None:
        """Malformed JSON lines should be skipped."""
        jsonl_path = temp_dir / "malformed.jsonl"
        lines = [
            '{"type": "assistant", "message": {"role": "assistant", '
            '"content": [{"type": "text", "text": "Good response"}]}}',
            "{not valid json}",
            '{"type": "assistant", "message": {"role": "assistant", '
            '"content": [{"type": "text", "text": "Last good response"}]}}',
        ]
        jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = get_last_assistant_response(jsonl_path)
        assert result == "Last good response"

    def test_tool_use_without_text_skipped(self, temp_dir: Path) -> None:
        """Assistant messages with only tool_use should not count as text response."""
        jsonl_path = temp_dir / "tool_only.jsonl"
        lines = [
            '{"type": "assistant", "message": {"role": "assistant", '
            '"content": [{"type": "text", "text": "Text response"}]}}',
            '{"type": "assistant", "message": {"role": "assistant", '
            '"content": [{"type": "tool_use", "name": "Read"}]}}',
        ]
        jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = get_last_assistant_response(jsonl_path)
        assert result == "Text response"


class TestGetFirstUserPrompt:
    """Tests for get_first_user_prompt function."""

    def test_string_content_returned(self, temp_dir: Path) -> None:
        """Subagent transcripts store the first prompt as a plain string."""
        jsonl_path = temp_dir / "subagent.jsonl"
        jsonl_path.write_text(
            '{"type": "user", "message": {"role": "user", "content": "Write the spec"}}\n',
            encoding="utf-8",
        )
        assert get_first_user_prompt(jsonl_path) == "Write the spec"

    def test_text_blocks_joined(self, temp_dir: Path) -> None:
        """List content should join its text blocks."""
        jsonl_path = temp_dir / "blocks.jsonl"
        jsonl_path.write_text(
            '{"type": "user", "message": {"role": "user", "content": '
            '[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}\n',
            encoding="utf-8",
        )
        assert get_first_user_prompt(jsonl_path) == "a\nb"

    def test_tool_result_only_skipped(self, temp_dir: Path) -> None:
        """Tool-result-only messages should be skipped."""
        jsonl_path = temp_dir / "tool_result.jsonl"
        lines = [
            '{"type": "user", "message": {"role": "user", "content": '
            '[{"type": "tool_result", "content": "x"}]}}',
            '{"type": "user", "message": {"role": "user", "content": "Real prompt"}}',
        ]
        jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert get_first_user_prompt(jsonl_path) == "Real prompt"

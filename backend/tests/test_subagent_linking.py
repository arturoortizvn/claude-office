"""Tests for subagent resolution and fallback linking logic."""

import json
from pathlib import Path

from app.core.state_machine import ResolvedAgent, resolve_agent_for_stop
from app.models.agents import Agent, AgentState


def create_test_agent(
    agent_id: str,
    native_id: str | None = None,
    state: AgentState = AgentState.ARRIVING,
) -> Agent:
    """Create a test agent with minimal required fields."""
    return Agent(
        id=agent_id,
        native_id=native_id,
        name=f"Agent-{agent_id[-4:]}",
        color="#3B82F6",
        number=1,
        state=state,
    )


def write_meta(directory: Path, native_id: str, tool_use_id: str) -> str:
    """Write a Claude Code subagent meta file and return its transcript path."""
    transcript = directory / f"agent-{native_id}.jsonl"
    transcript.write_text("")
    (directory / f"agent-{native_id}.meta.json").write_text(json.dumps({"toolUseId": tool_use_id}))
    return str(transcript)


class TestResolveAgentForStop:
    """Tests for resolve_agent_for_stop function."""

    def test_direct_agent_id_match(self) -> None:
        """Should resolve by direct agent_id when present."""
        agents = {"agent_123": create_test_agent("agent_123", native_id="abc123")}

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id="agent_123",
            native_agent_id="abc123",
        )

        assert result is not None
        assert result.agent_id == "agent_123"
        assert result.agent.native_id == "abc123"
        assert not result.was_late_linked

    def test_agent_id_match_ignores_native_id(self) -> None:
        """Direct agent_id should take precedence over native_id lookup."""
        agents = {
            "agent_123": create_test_agent("agent_123", native_id="abc123"),
            "agent_456": create_test_agent("agent_456", native_id="def456"),
        }

        # Even though native_id matches agent_456, agent_id should win
        result = resolve_agent_for_stop(
            agents=agents,
            agent_id="agent_123",
            native_agent_id="def456",  # This belongs to agent_456
        )

        assert result is not None
        assert result.agent_id == "agent_123"

    def test_native_id_match(self) -> None:
        """Should resolve by native_id when agent_id is not provided."""
        agents = {"subagent_abc123": create_test_agent("subagent_abc123", native_id="abc123")}

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id=None,
            native_agent_id="abc123",
        )

        assert result is not None
        assert result.agent_id == "subagent_abc123"
        assert result.agent.native_id == "abc123"
        assert not result.was_late_linked

    def test_fallback_links_the_agent_named_in_the_transcript_meta(self, tmp_path: Path) -> None:
        """Should late-link the agent whose tool_use_id the transcript meta names."""
        agents = {
            "subagent_toolu_old": create_test_agent("subagent_toolu_old", native_id=None),
            "subagent_toolu_mine": create_test_agent("subagent_toolu_mine", native_id=None),
        }

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id=None,
            native_agent_id="new_native_id",
            transcript_path=write_meta(tmp_path, "new_native_id", "toolu_mine"),
        )

        assert result is not None
        assert result.agent_id == "subagent_toolu_mine"
        assert result.agent.native_id == "new_native_id"
        assert result.was_late_linked
        assert agents["subagent_toolu_old"].native_id is None

    def test_stop_without_transcript_meta_leaves_unlinked_agents_alone(
        self, tmp_path: Path
    ) -> None:
        """A stop for an agent that never started here must not evict another agent."""
        agents = {"subagent_toolu_1": create_test_agent("subagent_toolu_1", native_id=None)}

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id=None,
            native_agent_id="phantom_native_id",
            transcript_path=str(tmp_path / "agent-phantom_native_id.jsonl"),
        )

        assert result is None
        assert agents["subagent_toolu_1"].native_id is None

    def test_meta_naming_an_unknown_tool_use_resolves_nothing(self, tmp_path: Path) -> None:
        """A meta pointing at a tool_use this office never saw must not link anyone."""
        agents = {"subagent_toolu_1": create_test_agent("subagent_toolu_1", native_id=None)}

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id=None,
            native_agent_id="other_native_id",
            transcript_path=write_meta(tmp_path, "other_native_id", "toolu_elsewhere"),
        )

        assert result is None
        assert agents["subagent_toolu_1"].native_id is None

    def test_returns_none_when_no_match_and_no_unlinked(self) -> None:
        """Should return None when agent not found and all agents are linked."""
        agents = {
            "agent_001": create_test_agent("agent_001", native_id="native_001"),
            "agent_002": create_test_agent("agent_002", native_id="native_002"),
        }

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id=None,
            native_agent_id="unknown_native_id",
        )

        assert result is None

    def test_returns_none_when_no_ids_provided(self) -> None:
        """Should return None when neither agent_id nor native_agent_id provided."""
        agents = {"agent_001": create_test_agent("agent_001", native_id=None)}

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id=None,
            native_agent_id=None,
        )

        assert result is None

    def test_returns_none_when_agent_id_not_found_and_no_native_id(self) -> None:
        """Should return None when agent_id not found and no native_id to fallback."""
        agents = {"agent_001": create_test_agent("agent_001", native_id=None)}

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id="nonexistent_agent",
            native_agent_id=None,
        )

        assert result is None

    def test_empty_agents_returns_none(self) -> None:
        """Should return None when no agents exist."""
        agents: dict[str, Agent] = {}

        result = resolve_agent_for_stop(
            agents=agents,
            agent_id=None,
            native_agent_id="any_id",
        )

        assert result is None


class TestResolvedAgent:
    """Tests for ResolvedAgent dataclass."""

    def test_was_late_linked_defaults_to_false(self) -> None:
        """was_late_linked should default to False."""
        agent = create_test_agent("test", native_id="123")
        resolved = ResolvedAgent(agent_id="test", agent=agent)
        assert resolved.was_late_linked is False

    def test_was_late_linked_can_be_set_true(self) -> None:
        """was_late_linked can be explicitly set to True."""
        agent = create_test_agent("test", native_id=None)
        resolved = ResolvedAgent(agent_id="test", agent=agent, was_late_linked=True)
        assert resolved.was_late_linked is True

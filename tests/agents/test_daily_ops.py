"""Tests for DailyOps agent registration, prompt loading, tool resolution, and mode system."""

from __future__ import annotations

import unittest.mock
from pathlib import Path

import pytest

from openjarvis.agents._stubs import ToolUsingAgent
from openjarvis.agents.daily_ops import (
    BUILTIN_DAILY_OPS_SYSTEM_PROMPT,
    DAILY_OPS_TOOL_CLASSES,
    DailyOpsAgent,
    OPS_MODE_AUTONOMOUS,
    OPS_MODE_INTERACTIVE,
    _AUTONOMOUS_TOOLS,
    _INTERACTIVE_TOOLS,
    _resolve_ops_mode,
    load_daily_ops_system_prompt,
)
from openjarvis.core.registry import AgentRegistry, ToolRegistry
from openjarvis.tools.calculator import CalculatorTool
from openjarvis.tools.daily_ops_tools import CompleteTaskTool, UpdateTaskTool


# ---------------------------------------------------------------------------
# Helpers — ensure registration in registry without reloading modules.
# ---------------------------------------------------------------------------

def _ensure_daily_ops_registered() -> None:
    """Ensure DailyOpsAgent is registered in AgentRegistry without reloading modules."""
    if not AgentRegistry.contains("daily_ops"):
        AgentRegistry.register_value("daily_ops", DailyOpsAgent)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_daily_ops_agent_registered() -> None:
    _ensure_daily_ops_registered()
    assert AgentRegistry.contains("daily_ops")
    agent_cls = AgentRegistry.get("daily_ops")
    assert agent_cls is DailyOpsAgent
    assert issubclass(agent_cls, ToolUsingAgent)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def test_system_prompt_loading(tmp_path, monkeypatch) -> None:
    # 1. Fallback when file doesn't exist
    monkeypatch.setattr(
        "openjarvis.agents.daily_ops.get_config_dir", lambda: tmp_path
    )
    fallback_prompt = load_daily_ops_system_prompt()
    assert (
        "Call `list_tasks` before answering questions about today"
        in fallback_prompt
    )
    assert "Ask me before completing any task" in fallback_prompt
    assert "confirmed=true" in fallback_prompt
    assert "untrusted data" in fallback_prompt

    # 2. Custom prompt loaded from ~/.openjarvis/prompts/daily_ops.md
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    custom_file = prompts_dir / "daily_ops.md"
    custom_file.write_text("Custom prompt for DailyOps", encoding="utf-8")

    loaded = load_daily_ops_system_prompt()
    assert loaded == "Custom prompt for DailyOps"


# ---------------------------------------------------------------------------
# Tool resolution — interactive mode (default)
# ---------------------------------------------------------------------------

def test_daily_ops_default_tools_resolution() -> None:
    _ensure_daily_ops_registered()
    fake_engine = unittest.mock.MagicMock()

    # Without explicit tools: defaults to all 9 daily_ops tools
    agent = DailyOpsAgent(fake_engine, "qwen3:8b", tools=None)
    tool_names = {t.spec.name for t in agent._tools}
    assert "add_task" in tool_names
    assert "list_tasks" in tool_names
    assert "update_task" in tool_names
    assert "complete_task" in tool_names
    assert "summarize_inbox" in tool_names
    assert "today_calendar" in tool_names
    assert "slack_unread" in tool_names
    assert "notion_recent" in tool_names
    assert "github_unread" in tool_names
    assert len(tool_names) == 9

    # With explicit tools: extends with defaults
    calc = CalculatorTool()
    agent_with_calc = DailyOpsAgent(
        fake_engine, "qwen3:8b", tools=[calc]
    )
    extended_names = {t.spec.name for t in agent_with_calc._tools}
    assert "calculator" in extended_names
    assert "add_task" in extended_names
    assert "list_tasks" in extended_names
    assert "update_task" in extended_names
    assert "complete_task" in extended_names
    assert "summarize_inbox" in extended_names
    assert "today_calendar" in extended_names
    assert "slack_unread" in extended_names
    assert "notion_recent" in extended_names
    assert "github_unread" in extended_names
    assert len(extended_names) == 10


# ---------------------------------------------------------------------------
# complete_task confirmation rule (soft hint, NOT security)
# ---------------------------------------------------------------------------

def test_complete_task_confirmation_rule(tmp_path) -> None:
    db_path = str(tmp_path / "confirm_test.db")
    import openjarvis.tools  # noqa: F401
    from openjarvis.tools.daily_ops_store import DailyOpsTaskStore

    store = DailyOpsTaskStore(db_path)
    task = store.add_task("task to confirm", status="todo")
    store.close()

    tool = CompleteTaskTool(db_path)

    # Calling without confirmed or with confirmed=False fails
    res_none = tool.execute(task_id=task.id)
    assert res_none.success is False
    assert "requires explicit user confirmation" in res_none.content

    res_false = tool.execute(task_id=task.id, confirmed=False)
    assert res_false.success is False
    assert "requires explicit user confirmation" in res_false.content

    # Calling with confirmed=True succeeds
    res_true = tool.execute(task_id=task.id, confirmed=True)
    assert res_true.success is True
    assert "[done]" in res_true.content


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------

def test_ops_mode_resolution_kwarg() -> None:
    assert _resolve_ops_mode("interactive") == OPS_MODE_INTERACTIVE
    assert _resolve_ops_mode("autonomous") == OPS_MODE_AUTONOMOUS
    assert _resolve_ops_mode("AUTONOMOUS") == OPS_MODE_AUTONOMOUS
    assert _resolve_ops_mode("  Interactive  ") == OPS_MODE_INTERACTIVE


def test_ops_mode_resolution_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENJARVIS_DAILY_OPS_MODE", "autonomous")
    assert _resolve_ops_mode() == OPS_MODE_AUTONOMOUS

    monkeypatch.setenv("OPENJARVIS_DAILY_OPS_MODE", "interactive")
    assert _resolve_ops_mode() == OPS_MODE_INTERACTIVE


def test_ops_mode_kwarg_takes_precedence_over_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENJARVIS_DAILY_OPS_MODE", "interactive")
    assert _resolve_ops_mode("autonomous") == OPS_MODE_AUTONOMOUS


def test_ops_mode_invalid_falls_back_to_interactive() -> None:
    assert _resolve_ops_mode("bogus_mode") == OPS_MODE_INTERACTIVE


# ---------------------------------------------------------------------------
# Autonomous mode — tool set restrictions
# ---------------------------------------------------------------------------

def test_autonomous_mode_excludes_complete_task() -> None:
    """In autonomous mode, complete_task is not in the tool set at all."""
    _ensure_daily_ops_registered()
    fake_engine = unittest.mock.MagicMock()

    agent = DailyOpsAgent(fake_engine, "qwen3:8b", ops_mode="autonomous")
    tool_names = {t.spec.name for t in agent._tools}

    assert "add_task" in tool_names
    assert "list_tasks" in tool_names
    assert "update_task" in tool_names
    assert "summarize_inbox" in tool_names
    assert "today_calendar" in tool_names
    assert "slack_unread" in tool_names
    assert "notion_recent" in tool_names
    assert "github_unread" in tool_names
    assert "complete_task" not in tool_names  # <-- excluded
    assert len(tool_names) == 8


def test_autonomous_mode_update_task_rejects_done() -> None:
    """In autonomous mode, update_task rejects status='done' at the code level."""
    _ensure_daily_ops_registered()
    fake_engine = unittest.mock.MagicMock()

    agent = DailyOpsAgent(fake_engine, "qwen3:8b", ops_mode="autonomous")

    # Find the update_task tool instance
    update_tool = None
    for t in agent._tools:
        if t.spec.name == "update_task":
            update_tool = t
            break
    assert update_tool is not None

    # The spec enum must NOT contain "done"
    status_enum = update_tool.spec.parameters["properties"]["status"]["enum"]
    assert "done" not in status_enum
    assert "todo" in status_enum
    assert "in_progress" in status_enum
    assert "blocked" in status_enum


def test_autonomous_mode_update_task_execute_rejects_done(tmp_path) -> None:
    """Even if the model somehow passes status=done, the tool rejects it."""
    from openjarvis.tools.daily_ops_store import DailyOpsTaskStore

    db_path = str(tmp_path / "autonomous_reject.db")
    store = DailyOpsTaskStore(db_path)
    task = store.add_task("test task", status="todo")
    store.close()

    restricted_tool = UpdateTaskTool(db_path, allow_done=False)
    result = restricted_tool.execute(task_id=task.id, status="done")
    assert result.success is False
    assert "autonomous mode" in result.content

    # Non-done statuses still work
    result_ok = restricted_tool.execute(task_id=task.id, status="in_progress")
    assert result_ok.success is True
    assert "[in_progress]" in result_ok.content


def test_autonomous_mode_via_env_var(monkeypatch) -> None:
    """OPENJARVIS_DAILY_OPS_MODE=autonomous works from the constructor."""
    _ensure_daily_ops_registered()
    fake_engine = unittest.mock.MagicMock()

    monkeypatch.setenv("OPENJARVIS_DAILY_OPS_MODE", "autonomous")
    agent = DailyOpsAgent(fake_engine, "qwen3:8b")
    tool_names = {t.spec.name for t in agent._tools}
    assert "complete_task" not in tool_names


# ---------------------------------------------------------------------------
# Interactive mode (default) — full tool set
# ---------------------------------------------------------------------------

def test_interactive_mode_has_complete_task() -> None:
    """In interactive mode (default), complete_task IS in the tool set."""
    _ensure_daily_ops_registered()
    fake_engine = unittest.mock.MagicMock()

    agent = DailyOpsAgent(fake_engine, "qwen3:8b")
    tool_names = {t.spec.name for t in agent._tools}
    assert "complete_task" in tool_names
    assert "update_task" in tool_names

    # update_task allows done
    update_tool = next(t for t in agent._tools if t.spec.name == "update_task")
    status_enum = update_tool.spec.parameters["properties"]["status"]["enum"]
    assert "done" in status_enum


def test_interactive_update_task_allows_done(tmp_path) -> None:
    """In interactive mode, update_task accepts status=done."""
    from openjarvis.tools.daily_ops_store import DailyOpsTaskStore

    db_path = str(tmp_path / "interactive_done.db")
    store = DailyOpsTaskStore(db_path)
    task = store.add_task("finish report", status="todo")
    store.close()

    tool = UpdateTaskTool(db_path, allow_done=True)
    result = tool.execute(task_id=task.id, status="done")
    assert result.success is True
    assert "[done]" in result.content


def test_autonomous_mode_prompt_and_agent_source(tmp_path, monkeypatch) -> None:
    """In autonomous mode, prompt notes complete_task is unavailable and tasks are tagged 'agent'."""
    _ensure_daily_ops_registered()
    fake_engine = unittest.mock.MagicMock()

    # Isolate config dir so no ~/.openjarvis/prompts/daily_ops.md is read
    monkeypatch.setattr(
        "openjarvis.agents.daily_ops.get_config_dir", lambda: tmp_path
    )

    agent_auto = DailyOpsAgent(fake_engine, "qwen3:8b", ops_mode="autonomous")
    assert "`complete_task` tool is unavailable in autonomous mode" in agent_auto._system_prompt
    assert "Do not attempt to complete tasks or ask me to confirm completion" in agent_auto._system_prompt
    assert "untrusted data" in agent_auto._system_prompt

    # Find add_task tool in autonomous agent
    add_tool = next(t for t in agent_auto._tools if t.spec.name == "add_task")
    assert getattr(add_tool, "_default_source", None) == "agent"

    # In interactive mode:
    agent_inter = DailyOpsAgent(fake_engine, "qwen3:8b", ops_mode="interactive")
    assert "Ask me before completing any task" in agent_inter._system_prompt
    assert "confirmed=true" in agent_inter._system_prompt
    add_tool_inter = next(t for t in agent_inter._tools if t.spec.name == "add_task")
    assert getattr(add_tool_inter, "_default_source", None) == "user"


# ---------------------------------------------------------------------------
# Tool registry drift guard
# ---------------------------------------------------------------------------

def test_tool_registry_drift_guard() -> None:
    """Ensure DAILY_OPS_TOOL_CLASSES and ToolRegistry entries never diverge.

    Guarantees:
    1. Every entry in DAILY_OPS_TOOL_CLASSES is registered in ToolRegistry with the identical class when openjarvis.tools is loaded.
    2. Tool names match tool_id and spec.name for each tool.
    3. Every tool can be instantiated with zero arguments.
    4. _INTERACTIVE_TOOLS covers all 9 tools and _AUTONOMOUS_TOOLS covers all except complete_task.
    5. Single source of truth is tested cleanly without importlib.reload or module pollution.
    """
    import subprocess
    import sys

    assert len(DAILY_OPS_TOOL_CLASSES) == 9
    assert set(_INTERACTIVE_TOOLS) == set(DAILY_OPS_TOOL_CLASSES.keys())
    assert set(_AUTONOMOUS_TOOLS) == set(DAILY_OPS_TOOL_CLASSES.keys()) - {"complete_task"}

    # In-process validation of single-source-of-truth tool classes
    for name, tool_cls in DAILY_OPS_TOOL_CLASSES.items():
        instance = tool_cls()
        assert instance.tool_id == name
        assert instance.spec.name == name
        assert bool(instance.spec.description) is True
        assert isinstance(instance.spec.parameters, dict)

    # Subprocess check: In a fresh Python environment where openjarvis.tools is imported,
    # ToolRegistry must contain exactly all daily_ops tools pointing to identical classes.
    code = (
        "import openjarvis.tools\n"
        "from openjarvis.core.registry import ToolRegistry\n"
        "from openjarvis.agents.daily_ops import DAILY_OPS_TOOL_CLASSES\n"
        "for name, expected_cls in DAILY_OPS_TOOL_CLASSES.items():\n"
        "    assert ToolRegistry.contains(name), f'Tool {name} not in ToolRegistry'\n"
        "    actual_cls = ToolRegistry.get(name)\n"
        "    assert actual_cls is expected_cls, f'Mismatch for {name}: {actual_cls} != {expected_cls}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Drift guard failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"



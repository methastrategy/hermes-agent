"""Tests for the adapter-native ``/discord-commands`` slash command.

The command answers with a snapshot of the registered native command tree plus the
last slash-command sync outcome. It is adapter-native on purpose: the answer is
static, so it must never cost an LLM turn (unlike the other native commands,
which render a command-text template and go through ``handle_message``).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import sys

import pytest

from gateway.config import PlatformConfig

# Reuse the discord stub bootstrap + FakeTree from the sibling slash test module
# so both suites see the same fake app_commands surface.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_discord_slash_commands import FakeTree, _ensure_discord_mock

_ensure_discord_mock()

from plugins.platforms.discord.adapter import DiscordAdapter


@pytest.fixture
def adapter():
    config = PlatformConfig(enabled=True, token="***")
    adapter = DiscordAdapter(config)
    adapter._client = SimpleNamespace(
        tree=FakeTree(),
        get_channel=lambda _id: None,
        fetch_channel=AsyncMock(),
        user=SimpleNamespace(id=99999, name="HermesBot"),
        application_id=123456789,
    )
    adapter._check_slash_authorization = AsyncMock(return_value=True)
    adapter._read_command_sync_state = lambda: {
        "123456789": {
            "fingerprint": "abc123def456",
            "last_success_at": 1700000000.0,
            "summary": {"total": 5, "created": 1, "updated": 0, "recreated": 0, "deleted": 0},
        }
    }
    return adapter


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registers_native_discord_commands_slash(adapter):
    """``/discord-commands`` must land on the tree as its own native command."""
    adapter._register_slash_commands()

    assert "discord-commands" in adapter._client.tree.commands


@pytest.mark.asyncio
async def test_discord_commands_closure_delegates_to_adapter_handler(adapter):
    """The closure only forwards; the handler owns the auth gate and the response."""
    adapter._handle_discord_commands_slash = AsyncMock()
    adapter._register_slash_commands()

    command = adapter._client.tree.commands["discord-commands"]
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

    await command(interaction)

    adapter._handle_discord_commands_slash.assert_awaited_once_with(interaction)


# ------------------------------------------------------------------
# Handler behavior
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discord_commands_handler_reports_inventory_ephemerally(adapter):
    """The reply lists the registered commands and the sync state, as an ephemeral message."""
    adapter._client.tree.commands["/help"] = SimpleNamespace(name="help", description="Show commands")
    adapter._client.tree.commands["/status"] = SimpleNamespace(name="status", description="Session info")
    send_message = AsyncMock()
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=send_message))

    await adapter._handle_discord_commands_slash(interaction)

    send_message.assert_awaited_once()
    kwargs = send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    content = send_message.await_args.args[0]
    assert "/help" in content
    assert "/status" in content
    assert "safe" in content  # default sync policy
    assert "1700000000" in content  # last_success_at


@pytest.mark.asyncio
async def test_discord_commands_handler_rejects_unauthorized(adapter):
    """An unauthorized user gets no inventory and no response body."""
    adapter._check_slash_authorization = AsyncMock(return_value=False)
    send_message = AsyncMock()
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=send_message))

    await adapter._handle_discord_commands_slash(interaction)

    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_discord_commands_handler_never_reaches_the_agent(adapter):
    """Adapter-native: the command must not dispatch a message to the LLM."""
    adapter.handle_message = AsyncMock()
    adapter._client.tree.commands["/help"] = SimpleNamespace(name="help", description="Show commands")
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

    await adapter._handle_discord_commands_slash(interaction)

    adapter.handle_message.assert_not_awaited()


# ------------------------------------------------------------------
# DISCORD_AUTO_REGISTER_COMMANDS toggle
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_register_disabled_keeps_only_curated_native_set(adapter):
    """With the toggle off, COMMAND_REGISTRY commands must not join the tree."""
    adapter._auto_register_commands_enabled = lambda: False
    adapter._run_simple_slash = AsyncMock()

    adapter._register_slash_commands()

    names = set(adapter._client.tree.commands)
    # Curated native commands are present.
    assert "thread" in names
    assert "discord-commands" in names
    # Registry-driven commands are not.
    assert "goal" not in names
    assert "subgoal" not in names


@pytest.mark.asyncio
async def test_auto_register_enabled_keeps_registry_commands(adapter):
    """The default (toggle on) still exposes COMMAND_REGISTRY commands natively."""
    assert adapter._auto_register_commands_enabled() is True
    adapter._run_simple_slash = AsyncMock()

    adapter._register_slash_commands()

    names = set(adapter._client.tree.commands)
    assert "thread" in names
    assert "discord-commands" in names
    assert "goal" in names

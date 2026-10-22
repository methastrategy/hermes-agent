"""Verify this install's update settings keep the feature branch alive.

Run from the repo root so the repo's packages are importable:

    cd C:/Users/hotme/AppData/Local/hermes/hermes-agent
    uv run --group dev python -m pytest C:/Users/hotme/hermes-safety-check/test_update_safety.py -v

It lives OUTSIDE the repo on purpose: the repo's own home_io_guard forbids
reading the production Hermes home from inside the test suite, and this check
is about the live install, not about the codebase.
"""
from pathlib import Path

import pytest

from hermes_cli.config import read_user_config_raw
from hermes_cli.update_channel import resolve_update_channel
from hermes_cli.update_cmd import _current_branch_name
from hermes_cli.update_cmd_git import _assess_parked_branch_switch, _git_run

ROOT = Path(r"C:\Users\hotme\AppData\Local\hermes\hermes-agent")
REAL_HOME = Path(r"C:\Users\hotme\AppData\Local\hermes")
BRANCH = "feature/discord-native-slash-commands"
GIT = ["git"]

# This check is ABOUT the live install (its config and its checkout), so the
# real-home guard must stand down. The sanctioned opt-out, per conftest.
pytestmark = pytest.mark.allow_real_home_io


@pytest.fixture(scope="module")
def production_config():
    """The install's REAL config, read the way Hermes reads it."""
    from hermes_cli.config import get_config_path
    import hermes_cli.config as config_mod

    path = REAL_HOME / "config.yaml"
    if not path.exists():
        pytest.skip(f"no production config at {path}")
    # The config module caches on a file signature; drop it so the real file
    # is actually re-read rather than the sandbox's.
    for attr in ("_CONFIG_CACHE", "_config_cache", "_RAW_CONFIG_CACHE"):
        if hasattr(config_mod, attr):
            try:
                setattr(config_mod, attr, None)
            except Exception:
                pass
    return read_user_config_raw(path) or {}


def test_update_preserves_the_feature_branch(production_config):
    cfg = production_config
    updates = cfg.get("updates") or {}

    # 1. The channel must be `main`: the stable channel resolves to a release
    #    TAG, and a release lands detached at that tag -- the parked-branch
    #    machinery is main-only, so the feature branch would be left behind.
    assert resolve_update_channel(cfg, ROOT) == "main"

    # 2. The checkout must sit ON the feature branch. A detached HEAD makes
    #    the update switch to `main` and drop the local commits.
    assert _current_branch_name(GIT) == BRANCH

    # 3. The strategy must merge upstream INTO the branch, not switch away.
    assert updates.get("parked_branch_strategy", "switch") == "update_in_place"

    # 4. auto_switch must not be disabled, or the whole update is skipped.
    assert updates.get("auto_switch_parked_branch", True) is True


def test_update_guard_passes_on_this_checkout():
    """The real guard must not refuse the update (dirty tree / disabled)."""
    # Only TRACKED changes matter: an untracked file does not make the tree
    # dirty for the update guard (it stashes tracked modifications).
    status = _git_run(
        GIT, ["status", "--porcelain", "--untracked-files=no"], ROOT
    ).stdout.strip()
    assert not status, f"tracked changes pending: {status}"

    switch_safe, reason = _assess_parked_branch_switch(
        GIT, ROOT, _current_branch_name(GIT), "main"
    )
    # "unmerged:<n>" = the local commits are not in origin/main, which is
    # exactly the case update_in_place handles by merging.
    assert switch_safe, f"the update would be refused: {reason}"
    assert reason.startswith("unmerged:"), reason


def test_feature_code_is_present_in_the_checkout():
    """The /agy command and the guild-scoped sync are in the running tree."""
    adapter = (ROOT / "plugins" / "platforms" / "discord" / "adapter.py").read_text(
        encoding="utf-8"
    )
    assert "_register_agy_slash" in adapter
    assert "_get_discord_sync_guild_id" in adapter
    assert "_refresh_agy_catalog_state" in adapter


def test_backup_layers_exist():
    """The patch backup is in place and the branch is pushed to the fork."""
    import subprocess

    patches = sorted((REAL_HOME / "backups").glob("*.patch"))
    assert len(patches) >= 6, f"expected 6 patches, found {len(patches)}"

    pushed = subprocess.run(
        ["git", "ls-remote", "--heads", "fork", BRANCH],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=False,
    )
    assert pushed.returncode == 0, pushed.stderr
    assert BRANCH in pushed.stdout, f"{BRANCH} is not on the fork"

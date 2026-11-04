import asyncio
import logging
import re

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from typer import Typer
from typer.testing import CliRunner, Result

from engleks_api.cli import build_cli
from engleks_api.config import Settings
from engleks_api.domain.access import UserStatus
from engleks_api.domain.text_processing import Provider
from engleks_api.infra.db.engine import build_sessionmaker
from engleks_api.infra.db.repositories import SqlIdentityRepository

runner = CliRunner()
CODE_RE = r"[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{12}"


@pytest.fixture(autouse=True)
def _clean_logging(migrated_url: str):
    """Every command needs the schema, and none may rely on logging that Alembic configured."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    structlog.reset_defaults()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()


async def invoke(cli: Typer, args: list[str]) -> Result:
    # Each command calls asyncio.run, which cannot share the test's running loop.
    return await asyncio.to_thread(runner.invoke, cli, args)


async def _create_invite(cli: Typer, *args: str) -> str:
    result = await invoke(cli, ["invite", "create", *args])
    assert result.exit_code == 0
    return result.stdout.splitlines()[0]


async def _find_user(engine: AsyncEngine, external_id: str):
    # A separate engine from the fixture's rollback connection: the CLI commits for real.
    sessions: async_sessionmaker[AsyncSession] = build_sessionmaker(engine)
    return await SqlIdentityRepository(sessions).find_user(Provider.TELEGRAM, external_id)


async def test_invite_create_prints_code_and_link(pg_settings: Settings):
    cli = build_cli(pg_settings.model_copy(update={"bot_username": "engleks_bot"}))
    r = await invoke(cli, ["invite", "create", "--note", "Anna", "--uses", "2", "--days", "7"])
    code, link = r.stdout.splitlines()
    assert r.exit_code == 0 and re.fullmatch(CODE_RE, code)
    assert link == f"https://t.me/engleks_bot?start={code}"


async def test_invite_create_without_bot_username_prints_only_code(pg_settings: Settings):
    r = await invoke(build_cli(pg_settings), ["invite", "create"])
    assert r.exit_code == 0
    assert re.fullmatch(CODE_RE, r.stdout.strip())


async def test_invite_create_with_empty_bot_username_prints_only_code(pg_settings: Settings):
    cli = build_cli(pg_settings.model_copy(update={"bot_username": ""}))
    r = await invoke(cli, ["invite", "create"])
    assert len(r.stdout.splitlines()) == 1


async def test_logs_go_to_stderr_and_stdout_stays_clean(pg_settings: Settings):
    cli = build_cli(pg_settings.model_copy(update={"bot_username": "engleks_bot"}))
    r = await invoke(cli, ["invite", "create"])
    code, link = r.stdout.splitlines()
    assert re.fullmatch(CODE_RE, code) and link.startswith("https://t.me/engleks_bot?start=")
    assert "invite_created" in r.stderr
    assert code not in r.stderr


async def test_invite_create_rejects_zero_uses(pg_settings: Settings):
    r = await invoke(build_cli(pg_settings), ["invite", "create", "--uses", "0"])
    assert r.exit_code == 2


async def test_invite_list_shows_created_invite(pg_settings: Settings):
    cli = build_cli(pg_settings)
    code = await _create_invite(cli, "--note", "Anna", "--uses", "2", "--days", "7")
    r = await invoke(cli, ["invite", "list"])
    assert r.exit_code == 0
    assert r.stdout.splitlines()[0] == "code\tnote\tuses\texpires\trevoked"
    row = next(line for line in r.stdout.splitlines() if line.startswith(code))
    assert re.fullmatch(rf"{code}\tAnna\t0/2\t\d{{4}}-\d\d-\d\d \d\d:\d\d\t-", row)


async def test_invite_list_shows_never_and_dash_for_a_bare_invite(pg_settings: Settings):
    cli = build_cli(pg_settings)
    code = await _create_invite(cli)
    r = await invoke(cli, ["invite", "list"])
    assert f"{code}\t-\t0/1\tnever\t-" in r.stdout.splitlines()


async def test_invite_revoke(pg_settings: Settings):
    cli = build_cli(pg_settings)
    code = await _create_invite(cli)
    r = await invoke(cli, ["invite", "revoke", code.lower()])
    assert r.exit_code == 0 and r.stdout.strip() == f"Revoked {code}"
    listing = await invoke(cli, ["invite", "list"])
    row = next(line for line in listing.stdout.splitlines() if line.startswith(code))
    assert re.search(r"\t\d{4}-\d\d-\d\d \d\d:\d\d$", row)


async def test_invite_revoke_unknown(pg_settings: Settings):
    r = await invoke(build_cli(pg_settings), ["invite", "revoke", "NOPE"])
    assert r.exit_code == 1
    assert "Unknown invite code: NOPE" in r.output


async def test_user_list_shows_user_without_usage(
    pg_settings: Settings, register, engine: AsyncEngine
):
    _, ext = await _register_committed(register, engine)
    r = await invoke(build_cli(pg_settings), ["user", "list"])
    assert r.exit_code == 0
    assert r.stdout.splitlines()[0] == (
        "telegram_id\tstatus\tper_minute\tdaily_tokens\trequests_today\ttokens_today"
    )
    assert f"{ext}\tactive\t-\t-\t0\t0" in r.stdout.splitlines()


async def test_user_disable_and_enable(pg_settings: Settings, register, engine: AsyncEngine):
    _, ext = await _register_committed(register, engine)
    cli = build_cli(pg_settings)
    r = await invoke(cli, ["user", "disable", ext])
    assert r.exit_code == 0 and r.stdout.strip() == f"Disabled {ext}"
    user = await _find_user(engine, ext)
    assert user is not None and user.status is UserStatus.DISABLED
    r = await invoke(cli, ["user", "enable", ext])
    assert r.exit_code == 0 and r.stdout.strip() == f"Enabled {ext}"
    user = await _find_user(engine, ext)
    assert user is not None and user.status is UserStatus.ACTIVE


async def test_user_disable_unknown(pg_settings: Settings):
    r = await invoke(build_cli(pg_settings), ["user", "disable", "999999999"])
    assert r.exit_code == 1
    assert "Unknown Telegram id: 999999999" in r.output


async def test_set_limits_keeps_the_other_override(
    pg_settings: Settings, register, engine: AsyncEngine
):
    _, ext = await _register_committed(register, engine, daily_token_budget=500)
    cli = build_cli(pg_settings)
    r = await invoke(cli, ["user", "set-limits", ext, "--per-minute", "5"])
    assert r.exit_code == 0 and r.stdout.strip() == f"Updated limits for {ext}"
    user = await _find_user(engine, ext)
    assert user is not None
    assert (user.rate_limit_per_minute, user.daily_token_budget) == (5, 500)
    r = await invoke(cli, ["user", "set-limits", ext, "--reset"])
    assert r.exit_code == 0
    user = await _find_user(engine, ext)
    assert user is not None
    assert (user.rate_limit_per_minute, user.daily_token_budget) == (None, None)


async def test_set_limits_unknown_user(pg_settings: Settings):
    r = await invoke(build_cli(pg_settings), ["user", "set-limits", "999999999", "--reset"])
    assert r.exit_code == 1
    assert "Unknown Telegram id: 999999999" in r.output


async def test_set_limits_needs_an_option(pg_settings: Settings, register, engine: AsyncEngine):
    _, ext = await _register_committed(register, engine)
    r = await invoke(build_cli(pg_settings), ["user", "set-limits", ext])
    assert r.exit_code == 2
    assert "Nothing to change: pass --per-minute, --daily-tokens or --reset" in r.output


async def test_set_limits_reset_with_value(pg_settings: Settings, register, engine: AsyncEngine):
    _, ext = await _register_committed(register, engine)
    r = await invoke(
        build_cli(pg_settings), ["user", "set-limits", ext, "--reset", "--per-minute", "5"]
    )
    assert r.exit_code == 2
    assert "--reset cannot be combined with --per-minute or --daily-tokens" in r.output


async def _register_committed(register, engine: AsyncEngine, **kwargs: int | None):
    # The CLI uses its own connections, so the user must be really committed: use a
    # sessionmaker on the engine, not the rollback-wrapped `sessions` fixture.
    return await register(build_sessionmaker(engine), **kwargs)

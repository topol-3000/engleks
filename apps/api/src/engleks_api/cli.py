"""engleks-admin: the admin use cases from a shell.

A second composition root next to main.py. Its output is the product, so it uses
`typer.echo`; the "never print" logging rule does not apply to it.
"""

import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import typer

from engleks_api.application.create_invite import create_invite
from engleks_api.application.list_invites import list_invites
from engleks_api.application.list_users import list_users
from engleks_api.application.revoke_invite import revoke_invite
from engleks_api.application.set_user_limits import set_user_limits
from engleks_api.application.set_user_status import set_user_status
from engleks_api.config import Settings
from engleks_api.domain.access import UserStatus
from engleks_api.infra.db.engine import build_engine, build_sessionmaker
from engleks_api.infra.db.repositories import (
    SqlIdentityRepository,
    SqlInviteRepository,
    SqlUserAdminRepository,
)
from engleks_api.logging_setup import configure_logging


@dataclass(frozen=True)
class _Repositories:
    identities: SqlIdentityRepository
    invites: SqlInviteRepository
    admin: SqlUserAdminRepository


def _fmt(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def build_cli(settings: Settings | None = None) -> typer.Typer:
    app = typer.Typer(help="Engleks admin commands.", no_args_is_help=True)
    invite_app = typer.Typer(help="Manage invite codes.", no_args_is_help=True)
    user_app = typer.Typer(help="Manage users.", no_args_is_help=True)
    app.add_typer(invite_app, name="invite")
    app.add_typer(user_app, name="user")

    @asynccontextmanager
    async def repositories() -> AsyncGenerator[tuple[_Repositories, Settings]]:
        # Built only when a command runs, so `--help` needs no environment.
        # pyright cannot see that pydantic-settings fills the required fields from the environment.
        resolved = settings or Settings()  # pyright: ignore[reportCallIssue]
        # stdout carries the command's output (scripts do `code=$(make invite)`): logs go to stderr.
        configure_logging(resolved.log_format, resolved.log_level, stream=sys.stderr)
        engine = build_engine(resolved.database_url())
        sessions = build_sessionmaker(engine)
        try:
            yield (
                _Repositories(
                    SqlIdentityRepository(sessions),
                    SqlInviteRepository(sessions),
                    SqlUserAdminRepository(sessions),
                ),
                resolved,
            )
        finally:
            await engine.dispose()

    def run[T](command: Callable[[_Repositories, Settings], Awaitable[T]]) -> T:
        async def go() -> T:
            async with repositories() as (repos, resolved):
                return await command(repos, resolved)

        return asyncio.run(go())

    @invite_app.command("create")
    def invite_create(
        note: Annotated[str | None, typer.Option(help="Who the code is for.")] = None,
        uses: Annotated[int, typer.Option(min=1, help="How many people can use it.")] = 1,
        days: Annotated[int | None, typer.Option(min=1, help="Valid for this many days.")] = None,
    ) -> None:
        """Create an invite code."""

        async def command(repos: _Repositories, resolved: Settings) -> list[str]:
            invite = await create_invite(repos.invites, note=note, max_uses=uses, valid_days=days)
            lines = [invite.code]
            if resolved.bot_username:
                lines.append(f"https://t.me/{resolved.bot_username}?start={invite.code}")
            return lines

        for line in run(command):
            typer.echo(line)

    @invite_app.command("list")
    def invite_list() -> None:
        """List invite codes, newest first."""

        async def command(repos: _Repositories, _: Settings) -> list[str]:
            rows = ["code\tnote\tuses\texpires\trevoked"]
            for invite in await list_invites(repos.invites):
                expires = "never" if invite.expires_at is None else _fmt(invite.expires_at)
                revoked = "-" if invite.revoked_at is None else _fmt(invite.revoked_at)
                rows.append(
                    f"{invite.code}\t{invite.note or '-'}\t"
                    f"{invite.used_count}/{invite.max_uses}\t{expires}\t{revoked}"
                )
            return rows

        for line in run(command):
            typer.echo(line)

    @invite_app.command("revoke")
    def invite_revoke(code: str) -> None:
        """Revoke an invite code."""

        async def command(repos: _Repositories, _: Settings) -> bool:
            return await revoke_invite(code, repos.invites)

        if not run(command):
            raise _fail(f"Unknown invite code: {code}")
        typer.echo(f"Revoked {code.upper()}")

    @user_app.command("list")
    def user_list() -> None:
        """List users with today's usage."""

        async def command(repos: _Repositories, _: Settings) -> list[str]:
            rows = ["telegram_id\tstatus\tper_minute\tdaily_tokens\trequests_today\ttokens_today"]
            for user in await list_users(repos.admin):
                per_minute = (
                    "-" if user.rate_limit_per_minute is None else user.rate_limit_per_minute
                )
                daily = "-" if user.daily_token_budget is None else user.daily_token_budget
                rows.append(
                    f"{user.external_id}\t{user.status.value}\t{per_minute}\t{daily}\t"
                    f"{user.requests_today}\t{user.tokens_today}"
                )
            return rows

        for line in run(command):
            typer.echo(line)

    def set_status(telegram_id: str, status: UserStatus, done: str) -> None:
        async def command(repos: _Repositories, _: Settings) -> bool:
            return await set_user_status(telegram_id, status, repos.admin)

        if not run(command):
            raise _fail(f"Unknown Telegram id: {telegram_id}")
        typer.echo(f"{done} {telegram_id}")

    @user_app.command("disable")
    def user_disable(telegram_id: str) -> None:
        """Block a user from using the service."""
        set_status(telegram_id, UserStatus.DISABLED, "Disabled")

    @user_app.command("enable")
    def user_enable(telegram_id: str) -> None:
        """Let a disabled user back in."""
        set_status(telegram_id, UserStatus.ACTIVE, "Enabled")

    @user_app.command("set-limits")
    def user_set_limits(
        telegram_id: str,
        per_minute: Annotated[
            int | None, typer.Option(min=1, help="Requests per minute for this user.")
        ] = None,
        daily_tokens: Annotated[
            int | None, typer.Option(min=1, help="Tokens per day for this user.")
        ] = None,
        reset: Annotated[bool, typer.Option(help="Go back to the default limits.")] = False,
    ) -> None:
        """Override a user's limits; limits you do not pass stay as they are."""
        if reset and (per_minute is not None or daily_tokens is not None):
            raise _fail("--reset cannot be combined with --per-minute or --daily-tokens", 2)
        if not reset and per_minute is None and daily_tokens is None:
            raise _fail("Nothing to change: pass --per-minute, --daily-tokens or --reset", 2)

        async def command(repos: _Repositories, _: Settings) -> bool:
            return await set_user_limits(
                telegram_id,
                repos.identities,
                repos.admin,
                per_minute=per_minute,
                daily_tokens=daily_tokens,
                reset=reset,
            )

        if not run(command):
            raise _fail(f"Unknown Telegram id: {telegram_id}")
        typer.echo(f"Updated limits for {telegram_id}")

    return app


def main() -> None:
    build_cli()()

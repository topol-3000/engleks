"""engleks-render-env: builds .env from deploy/prod.env, Infisical and the SHA."""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from .conftest import (
    DEFAULT_SECRETS,
    HC,
    NEW,
    SECRET_NAMES,
    Server,
    mode,
    rendered_env,
)

REQUIRED_CREDENTIAL_FIELDS = [
    "INFISICAL_PROJECT_ID",
    "INFISICAL_ENVIRONMENT",
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
]


def render(server: Server, out: Path, sha: str = NEW) -> subprocess.CompletedProcess[str]:
    return server.run("engleks-render-env", sha, str(out))


def secrets_hash(text: str) -> str:
    match = re.search(r"^ENGLEKS_SECRETS_HASH=(.*)$", text, re.MULTILINE)
    assert match
    return match.group(1)


def append_to(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def drop_credential_field(server: Server, field: str) -> None:
    lines = server.credentials.read_text().splitlines()
    kept = [line for line in lines if not line.startswith(f"{field}=")]
    server.credentials.write_text("\n".join(kept) + "\n")


def break_secret(server: Server, **changes: str | None) -> None:
    """Changes values in Infisical; None removes the key."""
    secrets = {**DEFAULT_SECRETS, **changes}
    server.set_secrets({k: v for k, v in secrets.items() if v is not None})


def test_writes_exact_file(server: Server, tmp_path: Path) -> None:
    out = tmp_path / "env.next"
    result = render(server, out)
    assert result.returncode == 0, result.stderr
    assert out.read_text() == rendered_env(NEW)
    assert mode(out) == 0o600


def test_hash_changes_only_with_secrets(server: Server, tmp_path: Path) -> None:
    first, second, rotated, other_sha = (tmp_path / n for n in ("a", "b", "c", "d"))
    assert render(server, first).returncode == 0
    assert render(server, second).returncode == 0
    assert secrets_hash(first.read_text()) == secrets_hash(second.read_text())

    break_secret(server, RESTIC_PASSWORD="another-restic")
    assert render(server, rotated).returncode == 0
    assert secrets_hash(rotated.read_text()) != secrets_hash(first.read_text())

    server.set_secrets(DEFAULT_SECRETS)
    assert render(server, other_sha, sha="c" * 40).returncode == 0
    assert secrets_hash(other_sha.read_text()) == secrets_hash(first.read_text())
    assert "ENGLEKS_TAG=" + "c" * 40 in other_sha.read_text()


def test_login_and_fetch_requests(server: Server, tmp_path: Path) -> None:
    assert render(server, tmp_path / "out").returncode == 0
    records = server.inputs().split(">>> ")[1:]
    login = next(r for r in records if "universal-auth/login" in r)
    fetch = next(r for r in records if "/secrets" in r)
    assert "client-secret-test" in login
    assert "client-id-test" in login
    assert "Bearer tok-test" in fetch
    fetch_call = fetch.splitlines()[0]
    assert "proj-test" in fetch_call
    assert "prod" in fetch_call


def test_missing_secret_fails(server: Server, tmp_path: Path) -> None:
    break_secret(server, API_LLM_API_KEY=None)
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert "API_LLM_API_KEY" in result.stderr


def test_empty_secret_fails(server: Server, tmp_path: Path) -> None:
    break_secret(server, API_LLM_API_KEY="")
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert "API_LLM_API_KEY" in result.stderr


@pytest.mark.parametrize("flagged", [True, False])
def test_hidden_secret_fails(server: Server, tmp_path: Path, flagged: bool) -> None:
    # An identity that may list but not read values gets secretValueHidden and the public
    # mask "<hidden-by-infisical>", which passes the value rule. It must not reach .env.
    value = "<hidden-by-infisical>"
    secrets = {**DEFAULT_SECRETS, "RESTIC_PASSWORD": value}
    server.set_secrets(secrets, hidden=("RESTIC_PASSWORD",) if flagged else ())
    out = tmp_path / "out"
    result = render(server, out)
    assert result.returncode == 1
    assert "RESTIC_PASSWORD" in result.stderr
    assert value not in result.stdout
    assert value not in result.stderr
    assert not out.exists()


def test_hidden_flag_fails_even_with_a_real_looking_value(server: Server, tmp_path: Path) -> None:
    server.set_secrets(DEFAULT_SECRETS, hidden=("API_LLM_API_KEY",))
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert "API_LLM_API_KEY" in result.stderr
    assert DEFAULT_SECRETS["API_LLM_API_KEY"] not in result.stderr


def test_name_in_both_sources_fails(server: Server, tmp_path: Path) -> None:
    append_to(server.root / "deploy" / "prod.env", "API_DB_PASSWORD=x\n")
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert "API_DB_PASSWORD" in result.stderr


@pytest.mark.parametrize(
    ("file", "line"),
    [
        ("prod.env", "ENGLEKS_TAG=x\n"),
        ("prod.env", "ENGLEKS_SECRETS_HASH=x\n"),
        ("secrets.txt", "ENGLEKS_TAG\n"),
        ("secrets.txt", "ENGLEKS_SECRETS_HASH\n"),
    ],
)
def test_reserved_name_fails(server: Server, tmp_path: Path, file: str, line: str) -> None:
    append_to(server.root / "deploy" / file, line)
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert line.split("=")[0].strip() in result.stderr


@pytest.mark.parametrize("value", ["a$b", "a'b", "a b", "tok\n", "tok ", "a#b", "a\\b"])
def test_rejects_value_compose_would_misread(server: Server, tmp_path: Path, value: str) -> None:
    break_secret(server, API_DB_PASSWORD=value)
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert "API_DB_PASSWORD" in result.stderr
    assert value not in result.stdout
    assert value not in result.stderr


def test_rejects_bad_prod_env_value(server: Server, tmp_path: Path) -> None:
    append_to(server.root / "deploy" / "prod.env", "API_BOT_USERNAME=a b\n")
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert "API_BOT_USERNAME" in result.stderr


def test_unknown_infisical_keys_ignored_with_warning(server: Server, tmp_path: Path) -> None:
    out = tmp_path / "out"
    break_secret(server, EXTRA_ONE="one-value", EXTRA_TWO="two-value")
    result = render(server, out)
    assert result.returncode == 0, result.stderr
    warnings = [line for line in result.stderr.splitlines() if "EXTRA_ONE" in line]
    assert len(warnings) == 1
    assert "EXTRA_TWO" in warnings[0]
    assert "one-value" not in result.stderr
    assert "EXTRA_" not in out.read_text()
    assert out.read_text() == rendered_env(NEW)


@pytest.mark.parametrize("sha", ["abc", "B" * 40])
def test_bad_sha_fails(server: Server, tmp_path: Path, sha: str) -> None:
    out = tmp_path / "out"
    result = render(server, out, sha=sha)
    assert result.returncode == 1
    assert not out.exists()


def test_missing_prod_env_fails_clearly(server: Server, tmp_path: Path) -> None:
    (server.root / "deploy" / "prod.env").unlink()
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert "deploy/prod.env" in result.stderr


@pytest.mark.parametrize("field", REQUIRED_CREDENTIAL_FIELDS)
def test_missing_credential_field_fails(server: Server, tmp_path: Path, field: str) -> None:
    drop_credential_field(server, field)
    result = render(server, tmp_path / "out")
    assert result.returncode == 1
    assert field in result.stderr


@pytest.mark.parametrize("failing", ["universal-auth", "/secrets"])
def test_login_or_fetch_failure(server: Server, tmp_path: Path, failing: str) -> None:
    result = server.run("engleks-render-env", NEW, str(tmp_path / "out"), STUB_FAIL=failing)
    assert result.returncode == 1


def _empty_secret(server: Server) -> None:
    break_secret(server, API_LLM_API_KEY="")


def _missing_secret(server: Server) -> None:
    break_secret(server, API_LLM_API_KEY=None)


def _bad_value(server: Server) -> None:
    break_secret(server, RESTIC_PASSWORD="a b")


def _both_sources(server: Server) -> None:
    append_to(server.root / "deploy" / "prod.env", "API_DB_PASSWORD=x\n")


def _no_prod_env(server: Server) -> None:
    (server.root / "deploy" / "prod.env").unlink()


def _no_credential(server: Server) -> None:
    drop_credential_field(server, "INFISICAL_CLIENT_SECRET")


@pytest.mark.parametrize(
    "break_it",
    [_empty_secret, _missing_secret, _bad_value, _both_sources, _no_prod_env, _no_credential],
)
def test_failure_leaves_output_untouched(
    server: Server, tmp_path: Path, break_it: Callable[[Server], None]
) -> None:
    out = tmp_path / "out"
    out.write_text("old\n")
    break_it(server)
    assert render(server, out).returncode == 1
    assert out.read_text() == "old\n"
    assert [p.name for p in tmp_path.glob("out.*")] == []


@pytest.mark.parametrize("failing", ["universal-auth", "/secrets"])
def test_request_failure_leaves_output_untouched(
    server: Server, tmp_path: Path, failing: str
) -> None:
    out = tmp_path / "out"
    out.write_text("old\n")
    result = server.run("engleks-render-env", NEW, str(out), STUB_FAIL=failing)
    assert result.returncode == 1
    assert out.read_text() == "old\n"
    assert [p.name for p in tmp_path.glob("out.*")] == []


def test_no_secret_in_output_or_argv(server: Server, tmp_path: Path) -> None:
    result = render(server, tmp_path / "out")
    assert result.returncode == 0, result.stderr
    secrets = [v for k, v in DEFAULT_SECRETS.items() if v != HC]
    assert len(secrets) == len(SECRET_NAMES) - 1
    leaked = [*secrets, "client-secret-test", "tok-test"]
    visible = "\n".join([*server.calls(), result.stdout, result.stderr])
    for value in leaked:
        assert value not in visible
    assert any(call.startswith("jq ") for call in server.calls())

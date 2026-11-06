import pytest

from .conftest import DEFAULT_ENV, HC, PREV, Server

FORGET = "restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune"


def backup(server: Server, **env: str | None):
    return server.run("engleks-backup", STUB_LS=str(server.backups), **env)


def pings(server: Server) -> list[str]:
    return [c for c in server.calls() if c.startswith("curl ")]


def last_ping_status(server: Server) -> str:
    last = pings(server)[-1]
    url = last.split(" @")[0].split()[-1]
    assert url.startswith(HC + "/")
    return url.removeprefix(HC + "/")


def test_success(server: Server):
    r = backup(server)
    assert r.returncode == 0, r.stderr
    calls = server.calls()
    assert len(calls) == 6, calls
    start, dump_app, dump_llm, backup_call, forget, done = calls
    assert start.startswith("curl ")
    assert start.endswith(f" {HC}/start @{PREV}")
    for dump, db in ((dump_app, '"$POSTGRES_DB"'), (dump_llm, "litellm")):
        assert dump.startswith("docker compose exec -T postgres ")
        assert "pg_dump" in dump
        assert "--format=custom --compress=0" in dump
        assert f"-d {db}" in dump
    assert backup_call.startswith(f"restic backup {server.backups} --tag nightly")
    assert backup_call.endswith("[engleks.dump litellm.dump]")
    assert forget.startswith(FORGET)
    assert done.startswith("curl ")
    assert done.endswith(f" {HC}/0 @{PREV}")
    assert list(server.backups.iterdir()) == []


def test_restic_failure_pings_fail_and_cleans_up(server: Server):
    r = backup(server, STUB_FAIL="^restic backup")
    assert r.returncode != 0
    assert last_ping_status(server) not in ("0", "start")
    assert list(server.backups.iterdir()) == []
    assert not any(c.startswith("restic forget") for c in server.calls())


def test_dump_failure_pings_fail(server: Server):
    r = backup(server, STUB_FAIL="pg_dump")
    assert r.returncode != 0
    assert not any(c.startswith("restic backup") for c in server.calls())
    assert last_ping_status(server) not in ("0", "start")
    assert list(server.backups.iterdir()) == []


def test_ping_failure_does_not_fail_backup(server: Server):
    r = backup(server, STUB_FAIL="^curl")
    assert r.returncode == 0, r.stderr
    assert any(c.startswith("restic backup") for c in server.calls())


@pytest.mark.usefixtures("held_lock")
def test_waits_for_deploy_lock(server: Server):
    r = backup(server)
    assert r.returncode != 0
    assert not any(c.startswith("docker") for c in server.calls())
    assert last_ping_status(server) not in ("0", "start")


@pytest.mark.parametrize("key", ["RESTIC_PASSWORD", "RESTIC_REPOSITORY", "HEALTHCHECKS_BACKUP_URL"])
def test_missing_config_exits_nonzero(server: Server, key: str):
    server.write_env("".join(line for line in DEFAULT_ENV.splitlines(True) if key not in line))
    r = backup(server)
    assert r.returncode == 1
    assert not any(c.startswith(("restic", "docker")) for c in server.calls())


def test_password_not_in_argv(server: Server):
    r = backup(server)
    assert r.returncode == 0, r.stderr
    assert server.calls()
    assert not any("s3cret-restic" in c for c in server.calls())
    assert "s3cret-restic" not in r.stdout + r.stderr

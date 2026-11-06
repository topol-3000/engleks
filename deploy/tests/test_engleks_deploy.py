import re
import subprocess

import pytest

from .conftest import (
    DEFAULT_ENV,
    DEFAULT_SECRETS,
    HC,
    NEW,
    PREV,
    SCRIPTS,
    TEST_PROD_ENV,
    Server,
    mode,
    rendered_env,
)

UP = "docker compose up -d --wait --wait-timeout 180"
UP_POSTGRES = "docker compose up -d --wait postgres"
PSQL = "docker compose exec -T postgres"
GENERATE_KEY = "docker compose exec -T litellm python /deploy/generate_key.py"
ENSURE = f"{GENERATE_KEY} --ensure-stdin --max-budget 10 --budget-duration 30d"
DELETE = f"{GENERATE_KEY} --delete-stdin"
OLD = "c" * 40
IMAGES = " ".join(f"ghcr.io/topol-3000/engleks-api:{t}" for t in (OLD, PREV, NEW))
FAIL_UP = "^docker compose up -d --wait --wait-timeout"


def deploy(server: Server, sha: str = NEW, **env: str | None):
    return server.run("engleks-deploy", SSH_ORIGINAL_COMMAND=sha, **env)


def checkouts(server: Server) -> list[str]:
    return [c for c in server.calls() if c.startswith("git checkout")]


def ops(server: Server) -> list[str]:
    """The git, docker and restic calls, without the render step's curl and jq."""
    return [c for c in server.calls() if c.startswith(("git ", "docker ", "restic "))]


def ALTER(role: str, value: str) -> str:
    """What engleks-deploy pipes into psql to set ROLE's password to VALUE.

    The SET lines come first: psql substitutes :'pw' client-side, so a failed ALTER would
    otherwise log the statement, password included, in the Postgres container log."""
    return (
        "SET log_min_error_statement = panic;\nSET log_statement = 'none';\n"
        f"\\set pw '{value}'\nALTER ROLE {role} PASSWORD :'pw';"
    )


def given_input(command: str, value: str) -> str:
    """The input-log entry of COMMAND reading VALUE on stdin."""
    return f">>> {command}\n{value}\n"


def secrets(**changes: str) -> dict[str, str]:
    return {**DEFAULT_SECRETS, **changes}


def index(calls: list[str], prefix: str, tag: str | None = None) -> int:
    return next(
        i
        for i, c in enumerate(calls)
        if c.startswith(prefix) and (tag is None or c.endswith(f"@{tag}"))
    )


@pytest.mark.parametrize("sha", ["", "abc", "B" * 40, NEW + "; rm -rf /", NEW + "0"])
def test_rejects_malformed_sha(server: Server, sha: str):
    r = deploy(server, sha)
    assert r.returncode == 2
    assert server.calls() == []
    assert server.env_text() == DEFAULT_ENV


def test_deploys_sha(server: Server):
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    o = ops(server)
    assert o[:2] == [f"git fetch origin @{PREV}", f"git checkout --detach {NEW} @{PREV}"]
    assert o[2] == f"docker compose pull @{NEW}"
    assert o[3].startswith(UP)
    assert o[3].endswith(f"@{NEW}")
    assert o[4] == f"{ENSURE} @{NEW}"
    assert all(c.startswith("docker image") for c in o[5:])
    assert not any(c.startswith("restic") for c in o)
    calls = server.calls()
    curls = [i for i, c in enumerate(calls) if c.startswith("curl")]
    assert len(curls) == 2
    checkout = calls.index(f"git checkout --detach {NEW} @{PREV}")
    pull = calls.index(f"docker compose pull @{NEW}")
    assert all(checkout < i < pull for i in curls)
    assert given_input(ENSURE, "sk-api-1") in server.inputs()


def test_env_is_rendered(server: Server):
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    assert server.env_text() == rendered_env(NEW)
    assert mode(server.env_file) == 0o600
    assert not (server.root / ".env.next").exists()
    assert not (server.root / ".env.previous").exists()


def test_sha_from_argument(server: Server):
    r = server.run("engleks-deploy", NEW)
    assert r.returncode == 0, r.stderr
    assert server.tag() == NEW


def test_redeploy_of_running_sha(server: Server):
    server.write_env(rendered_env(NEW))
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    assert server.env_text() == rendered_env(NEW)
    assert len(checkouts(server)) == 1


def test_first_deploy(server: Server):
    server.env_file.unlink()
    r = deploy(server, STUB_FAIL="^restic cat config", STUB_FAIL_CODE="10")
    assert r.returncode == 0, r.stderr
    calls = server.calls()
    assert calls.index("restic cat config @") < calls.index("restic init @")
    assert not any(c.startswith(UP_POSTGRES) for c in calls)
    assert "ALTER ROLE" not in server.inputs()
    assert not any(DEFAULT_SECRETS["RESTIC_PASSWORD"] in c for c in calls)
    assert server.env_text() == rendered_env(NEW)


@pytest.mark.parametrize("changed", ["RESTIC_PASSWORD", "RESTIC_REPOSITORY"])
def test_restic_checked_when_password_or_repository_changes(server: Server, changed: str):
    if changed == "RESTIC_PASSWORD":
        server.set_secrets(secrets(RESTIC_PASSWORD="another-restic"))
    else:
        prod_env = TEST_PROD_ENV.replace("sftp:storagebox:engleks", "sftp:storagebox:moved")
        (server.root / "deploy" / "prod.env").write_text(prod_env)
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    calls = server.calls()
    assert f"restic cat config @{PREV}" in calls
    assert not any(c.startswith("restic init") for c in calls)


def test_restic_failure_fails_deploy(server: Server):
    server.set_secrets(secrets(RESTIC_PASSWORD="another-restic"))
    r = deploy(server, STUB_FAIL="^restic cat config")
    assert r.returncode == 1
    assert not any(c.startswith("restic init") for c in server.calls())
    assert '"Changing a secret" in docs/deployment.md' in r.stdout + r.stderr
    assert server.env_text() == DEFAULT_ENV


def test_restic_failure_alters_no_role(server: Server):
    server.set_secrets(secrets(RESTIC_PASSWORD="another-restic", API_DB_PASSWORD="db-pw-2"))
    r = deploy(server, STUB_FAIL="^restic cat config")
    assert r.returncode == 1
    assert "ALTER ROLE" not in server.inputs()
    assert server.env_text() == DEFAULT_ENV


@pytest.mark.parametrize(
    ("key", "role"), [("API_DB_PASSWORD", "CURRENT_USER"), ("LITELLM_DB_PASSWORD", "litellm")]
)
def test_changed_db_password_alters_role_before_up(server: Server, key: str, role: str):
    server.set_secrets(secrets(**{key: "new-db-pw"}))
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    o = ops(server)
    assert index(o, UP_POSTGRES, NEW) < index(o, PSQL, NEW) < index(o, UP, NEW)
    assert ALTER(role, "new-db-pw") in server.inputs()
    assert sum(c.startswith(PSQL) for c in o) == 1
    assert not any("new-db-pw" in c for c in server.calls())


def test_unchanged_passwords_alter_nothing(server: Server):
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    assert not any(c.startswith((UP_POSTGRES, PSQL)) for c in server.calls())
    assert "ALTER ROLE" not in server.inputs()


def test_failed_deploy_restores_env_and_password(server: Server):
    server.set_secrets(secrets(API_DB_PASSWORD="db-pw-2"))
    r = deploy(server, STUB_FAIL=FAIL_UP, STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    assert server.env_text() == DEFAULT_ENV
    inputs = server.inputs()
    to_new = inputs.index(ALTER("CURRENT_USER", "db-pw-2"))
    assert inputs.index(ALTER("CURRENT_USER", "db-pw-1")) > to_new
    calls = server.calls()
    rollback_up = index(calls, UP, PREV)
    assert f"{ENSURE} @{PREV}" in calls[rollback_up + 1 :]
    assert inputs.rindex(given_input(ENSURE, "sk-api-1")) > to_new


def test_failed_same_sha_redeploy_restores_env_and_password(server: Server):
    server.write_env(rendered_env(NEW))
    server.set_secrets(secrets(API_DB_PASSWORD="db-pw-2"))
    r = deploy(server, STUB_FAIL=FAIL_UP)
    assert r.returncode == 1
    assert server.env_text() == rendered_env(NEW)
    inputs = server.inputs()
    assert inputs.index(ALTER("CURRENT_USER", "db-pw-1")) > inputs.index(
        ALTER("CURRENT_USER", "db-pw-2")
    )


def test_infisical_failure_leaves_server_untouched(server: Server):
    r = deploy(server, STUB_FAIL="universal-auth")
    assert r.returncode == 1
    assert server.env_text() == DEFAULT_ENV
    assert not any(
        c.startswith(("docker compose pull", "docker compose up")) and c.endswith(f"@{NEW}")
        for c in server.calls()
    )
    assert checkouts(server)[-1] == f"git checkout --detach {PREV} @{PREV}"


def test_key_rotation_deletes_old_key_after_ensure(server: Server):
    server.set_secrets(secrets(API_LLM_API_KEY="sk-api-2"))
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    inputs = server.inputs()
    ensure = inputs.index(given_input(ENSURE, "sk-api-2"))
    assert inputs.index(given_input(DELETE, "sk-api-1")) > ensure


def test_failed_old_key_delete_is_a_warning(server: Server):
    server.set_secrets(secrets(API_LLM_API_KEY="sk-api-2"))
    r = deploy(server, STUB_FAIL="delete-stdin")
    assert r.returncode == 0, r.stderr
    assert given_input(DELETE, "sk-api-1") in server.inputs()
    assert "warning" in r.stdout
    assert server.env_text() == rendered_env(NEW, secrets(API_LLM_API_KEY="sk-api-2"))


def test_unchanged_key_is_not_deleted(server: Server):
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    assert not any(c.startswith(DELETE) for c in server.calls())


def test_failed_ensure_rolls_back(server: Server):
    r = deploy(server, STUB_FAIL="ensure-stdin", STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    assert server.env_text() == DEFAULT_ENV
    assert f"{ENSURE} @{PREV}" in server.calls()


def test_failed_up_rolls_back(server: Server):
    r = deploy(server, STUB_FAIL=FAIL_UP, STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    calls = server.calls()
    after = calls[index(calls, UP, NEW) + 1 :]
    assert f"git checkout --detach {PREV} @{NEW}" in after
    assert any(c.startswith(UP) and c.endswith(f"@{PREV}") for c in after)
    assert server.env_text() == DEFAULT_ENV
    assert not (server.root / ".env.previous").exists()
    assert not (server.root / ".env.next").exists()


def test_failed_pull_skips_up(server: Server):
    r = deploy(server, STUB_FAIL="^docker compose pull", STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    assert not any(re.search(rf"compose up.* @{NEW}", c) for c in server.calls())
    assert server.tag() == PREV


def test_unknown_sha_rolls_back(server: Server):
    r = deploy(server, STUB_FAIL="^git checkout --detach b")
    assert r.returncode == 1
    assert not any(c.startswith(("curl", "docker compose pull")) for c in server.calls())
    assert checkouts(server)[-1] == f"git checkout --detach {PREV} @{PREV}"
    assert server.env_text() == DEFAULT_ENV


def test_stale_previous_env_is_not_restored(server: Server):
    # A killed run can leave .env.previous behind. If this run fails before it saves its own
    # copy, the rollback must keep the running .env, not restore the stale one.
    stale = server.root / ".env.previous"
    stale.write_text(rendered_env(PREV, secrets(API_DB_PASSWORD="stale-pw")))
    stale.chmod(0o600)
    r = deploy(server, STUB_FAIL="universal-auth")
    assert r.returncode == 1
    assert server.env_text() == DEFAULT_ENV
    assert "ALTER ROLE" not in server.inputs()


def test_failed_copy_of_env_does_not_clobber_env_on_rollback(server: Server):
    # A disk-full `install .env .env.previous` leaves a truncated .env.previous. The rollback
    # must not move it over the good .env.
    install = server.root.parent / "bin" / "install"
    install.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ ${*: -1} == *.env.previous ]]; then echo truncated > "${*: -1}"; exit 1; fi\n'
        'exec /usr/bin/install "$@"\n'
    )
    install.chmod(0o755)
    r = deploy(server)
    assert r.returncode == 1
    assert server.env_text() == DEFAULT_ENV
    assert not (server.root / ".env.previous").exists()


def test_failure_without_previous(server: Server):
    server.env_file.unlink()
    r = deploy(server, STUB_FAIL=FAIL_UP)
    assert r.returncode == 1
    assert len(checkouts(server)) == 1
    assert server.env_text() == rendered_env(NEW)


def test_no_secret_in_calls_or_output(server: Server):
    rotated = {
        "API_DB_PASSWORD": "db-pw-2",
        "LITELLM_DB_PASSWORD": "llm-db-pw-2",
        "API_LLM_API_KEY": "sk-api-2",
        "RESTIC_PASSWORD": "restic-pw-2",
        "HEALTHCHECKS_BACKUP_URL": HC,
    }
    server.set_secrets(rotated)
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    assert "ALTER ROLE litellm" in server.inputs()
    log = (server.root / ".deploy.log").read_text()
    calls = "\n".join(server.calls())
    for name, value in [*DEFAULT_SECRETS.items(), *rotated.items()]:
        if name == "HEALTHCHECKS_BACKUP_URL":
            continue
        for where in (calls, r.stdout, r.stderr, log):
            assert value not in where, name


def test_removes_old_images_on_success(server: Server):
    r = deploy(server, STUB_IMAGES=IMAGES)
    assert r.returncode == 0, r.stderr
    removed = [c for c in server.calls() if c.startswith("docker image rm")]
    assert removed == [f"docker image rm ghcr.io/topol-3000/engleks-api:{OLD} @{NEW}"]


def test_failed_image_cleanup_does_not_fail_deploy(server: Server):
    r = deploy(server, STUB_IMAGES=IMAGES, STUB_FAIL="^docker image rm")
    assert r.returncode == 0, r.stderr
    assert server.tag() == NEW


def test_no_cleanup_after_failed_deploy(server: Server):
    r = deploy(server, STUB_IMAGES=IMAGES, STUB_FAIL=FAIL_UP, STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    assert not any(c.startswith("docker image rm") for c in server.calls())


@pytest.mark.usefixtures("held_lock")
def test_waits_for_lock_then_gives_up(server: Server):
    # The running deploy's .env.next is its own: the waiting one must not remove it.
    theirs = server.root / ".env.next"
    theirs.write_text(rendered_env(NEW))
    r = deploy(server)
    assert r.returncode == 1
    assert server.calls() == []
    assert server.env_text() == DEFAULT_ENV
    assert theirs.exists()


def deploy_with_lost_client(server: Server, **env: str):
    """Runs a deploy whose stdout reader is gone, as when the CI job is cancelled."""
    full_env = {**server.base_env, "SSH_ORIGINAL_COMMAND": NEW, **env}
    proc = subprocess.Popen(
        ["bash", str(SCRIPTS / "engleks-deploy")],
        cwd=server.root,
        env=full_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    proc.stdout.close()
    return proc.wait(timeout=30)


def test_deploy_finishes_after_client_goes_away(server: Server):
    rc = deploy_with_lost_client(server)
    assert rc == 0
    assert any(c.startswith(UP) and c.endswith(f"@{NEW}") for c in server.calls())
    assert server.env_text() == rendered_env(NEW)
    assert "==> deployed" in (server.root / ".deploy.log").read_text()


def test_rollback_finishes_after_client_goes_away(server: Server):
    rc = deploy_with_lost_client(server, STUB_FAIL=FAIL_UP, STUB_FAIL_TAG=NEW)
    assert rc == 1
    assert server.env_text() == DEFAULT_ENV
    assert any(c.startswith(UP) and c.endswith(f"@{PREV}") for c in server.calls())


def test_rollback_works_offline(server: Server):
    # The new images could not be pulled: GHCR (and likely GitHub) is down. The previous
    # release's commit and images are local, so the rollback must not need the network.
    r = deploy(server, STUB_FAIL="^docker compose pull", STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    calls = server.calls()
    failed_pull = calls.index(f"docker compose pull @{NEW}")
    rollback = calls[failed_pull + 1 :]
    assert not any(c.startswith(("git fetch", "docker compose pull", "curl")) for c in rollback)
    assert f"git checkout --detach {PREV} @{NEW}" in rollback
    assert any(c.startswith(UP) and c.endswith(f"@{PREV}") for c in rollback)
    assert server.env_text() == DEFAULT_ENV

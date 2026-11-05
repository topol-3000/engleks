import re
import subprocess

import pytest

from .conftest import DEFAULT_ENV, NEW, PREV, SCRIPTS, Server, mode

UP = "docker compose up -d --wait --wait-timeout 180"
OLD = "c" * 40
IMAGES = " ".join(f"ghcr.io/topol-3000/engleks-api:{t}" for t in (OLD, PREV, NEW))


def deploy(server: Server, sha: str = NEW, **env: str | None):
    return server.run("engleks-deploy", SSH_ORIGINAL_COMMAND=sha, **env)


def checkouts(server: Server) -> list[str]:
    return [c for c in server.calls() if c.startswith("git checkout")]


@pytest.mark.parametrize("sha", ["", "abc", "B" * 40, NEW + "; rm -rf /", NEW + "0"])
def test_rejects_malformed_sha(server: Server, sha: str):
    r = deploy(server, sha)
    assert r.returncode == 2
    assert server.calls() == []
    assert server.env_text() == DEFAULT_ENV


def test_deploys_sha(server: Server):
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    calls = server.calls()
    assert calls[:3] == [
        f"git fetch origin @{PREV}",
        f"git checkout --detach {NEW} @{PREV}",
        f"docker compose pull @{NEW}",
    ]
    assert calls[3].startswith(UP)
    assert calls[3].endswith(f"@{NEW}")
    assert server.tag() == NEW


def test_sha_from_argument(server: Server):
    r = server.run("engleks-deploy", NEW)
    assert r.returncode == 0, r.stderr
    assert server.tag() == NEW


def test_deploy_keeps_rest_of_env_and_mode(server: Server):
    r = deploy(server)
    assert r.returncode == 0, r.stderr
    expected = DEFAULT_ENV.replace(f"ENGLEKS_TAG={PREV}", f"ENGLEKS_TAG={NEW}")
    assert server.env_text() == expected
    assert mode(server.env_file) == 0o600


def test_failed_up_rolls_back(server: Server):
    r = deploy(server, STUB_FAIL="^docker compose up", STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    calls = server.calls()
    failed_up = next(i for i, c in enumerate(calls) if c.startswith(UP) and c.endswith(f"@{NEW}"))
    after = calls[failed_up + 1 :]
    assert f"git checkout --detach {PREV} @{NEW}" in after
    assert any(c.startswith(UP) and c.endswith(f"@{PREV}") for c in after)
    assert server.tag() == PREV


def test_failed_pull_skips_up(server: Server):
    r = deploy(server, STUB_FAIL="^docker compose pull", STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    assert not any(re.search(rf"compose up.* @{NEW}", c) for c in server.calls())
    assert server.tag() == PREV


def test_unknown_sha_rolls_back(server: Server):
    r = deploy(server, STUB_FAIL="^git checkout --detach b")
    assert r.returncode == 1
    assert f"docker compose pull @{NEW}" not in server.calls()
    assert server.tag() == PREV


def test_failure_without_previous_exits_nonzero(server: Server):
    server.write_env(DEFAULT_ENV.replace(f"ENGLEKS_TAG={PREV}\n", ""))
    r = deploy(server, STUB_FAIL="^docker compose up")
    assert r.returncode == 1
    assert len(checkouts(server)) == 1
    assert server.env_text().endswith(f"ENGLEKS_TAG={NEW}\n")
    assert server.tag() == NEW


def test_redeploy_same_sha_failure_no_rollback(server: Server):
    server.write_env(DEFAULT_ENV.replace(PREV, NEW))
    r = deploy(server, STUB_FAIL="^docker compose up")
    assert r.returncode == 1
    assert len(checkouts(server)) == 1


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
    r = deploy(server, STUB_IMAGES=IMAGES, STUB_FAIL="^docker compose up", STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    assert not any(c.startswith("docker image rm") for c in server.calls())


@pytest.mark.usefixtures("held_lock")
def test_waits_for_lock_then_gives_up(server: Server):
    r = deploy(server)
    assert r.returncode == 1
    assert server.calls() == []
    assert server.tag() == PREV


def deploy_with_lost_client(server: Server, **env: str):
    """Runs a deploy whose stdout reader is gone, as when the CI job is cancelled."""
    full_env = {**server.base_env, "SSH_ORIGINAL_COMMAND": NEW, **env}
    proc = subprocess.Popen(
        ["bash", str(SCRIPTS / "engleks-deploy")],
        cwd=server.root,
        env=full_env,
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
    assert server.tag() == NEW
    assert "==> deployed" in (server.root / ".deploy.log").read_text()


def test_rollback_finishes_after_client_goes_away(server: Server):
    rc = deploy_with_lost_client(server, STUB_FAIL="^docker compose up", STUB_FAIL_TAG=NEW)
    assert rc == 1
    assert server.tag() == PREV
    assert any(c.startswith(UP) and c.endswith(f"@{PREV}") for c in server.calls())


def test_rollback_works_offline(server: Server):
    # The new images could not be pulled: GHCR (and likely GitHub) is down. The previous
    # release's commit and images are local, so the rollback must not need the network.
    r = deploy(server, STUB_FAIL="^docker compose pull", STUB_FAIL_TAG=NEW)
    assert r.returncode == 1
    calls = server.calls()
    failed_pull = calls.index(f"docker compose pull @{NEW}")
    rollback = calls[failed_pull + 1 :]
    assert not any(c.startswith(("git fetch", "docker compose pull")) for c in rollback)
    assert f"git checkout --detach {PREV} @{NEW}" in rollback
    assert any(c.startswith(UP) and c.endswith(f"@{PREV}") for c in rollback)
    assert server.tag() == PREV

"""Harness for the server scripts in deploy/server.

Each test gets a fake server: a checkout directory (ENGLEKS_DIR) with a .env, a backup
staging directory (BACKUP_DIR) and a bin directory whose git, docker, restic and curl are
one stub script. The stub logs every call with the ENGLEKS_TAG in .env at that moment,
fails calls matching STUB_FAIL (optionally only while the tag is STUB_FAIL_TAG) and prints
canned output for the few calls whose output the scripts read.
"""

import os
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "server"

PREV = "a" * 40
NEW = "b" * 40
HC = "https://hc-ping.com/test-uuid"

DEFAULT_ENV = f"""\
COMPOSE_FILE=compose.yaml:compose.prod.yaml
ENGLEKS_TAG={PREV}
API_DB_PASSWORD=secret
RESTIC_REPOSITORY=sftp:storagebox:engleks
RESTIC_PASSWORD=s3cret-restic
HEALTHCHECKS_BACKUP_URL={HC}
"""

STUB = r"""#!/usr/bin/env bash
name=$(basename "$0"); line="$name $*"
tag=$(sed -n 's/^ENGLEKS_TAG=//p' "$ENGLEKS_DIR/.env" 2>/dev/null | tail -n1)
extra=""
if [[ $name == restic && -n ${STUB_LS-} ]]; then
  extra=" [$(ls "$STUB_LS" | sort | xargs)]"
fi
echo "$line @$tag$extra" >> "$STUB_LOG"
if [[ -n ${STUB_FAIL-} && $line =~ $STUB_FAIL
      && ( -z ${STUB_FAIL_TAG-} || $tag == "${STUB_FAIL_TAG}" ) ]]; then
  exit 1
fi
case $line in
  "docker image ls"*) [[ -n ${STUB_IMAGES-} ]] && printf '%s\n' $STUB_IMAGES ;;
  *pg_dump*) echo dump ;;
  *generate_key.py*) echo "API_LLM_API_KEY=sk-generated" ;;
  "git rev-parse HEAD") echo "${STUB_HEAD-}" ;;
esac
exit 0
"""


@dataclass
class Server:
    root: Path
    backups: Path
    log: Path
    base_env: dict[str, str]

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @property
    def lock(self) -> Path:
        return self.root / ".deploy.lock"

    def write_env(self, text: str) -> None:
        self.env_file.write_text(text)
        self.env_file.chmod(0o600)

    def env_text(self) -> str:
        return self.env_file.read_text()

    def tag(self) -> str | None:
        tags = [
            line.removeprefix("ENGLEKS_TAG=")
            for line in self.env_text().splitlines()
            if line.startswith("ENGLEKS_TAG=")
        ]
        return tags[-1] if tags else None

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def run(
        self, script: str, *args: str, input: str | None = None, **env: str | None
    ) -> subprocess.CompletedProcess[str]:
        full_env = dict(self.base_env)
        for key, value in env.items():
            if value is None:
                full_env.pop(key, None)
            else:
                full_env[key] = value
        return subprocess.run(
            ["bash", str(SCRIPTS / script), *args],
            cwd=self.root,
            env=full_env,
            input=input,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


@pytest.fixture
def server(tmp_path: Path) -> Server:
    root = tmp_path / "engleks"
    backups = tmp_path / "backups"
    bin_dir = tmp_path / "bin"
    for d in (root, backups, bin_dir):
        d.mkdir()
    stub = bin_dir / "stub"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    for name in ("git", "docker", "restic", "curl"):
        (bin_dir / name).symlink_to(stub)
    log = tmp_path / "calls.log"
    base_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "ENGLEKS_DIR": str(root),
        "BACKUP_DIR": str(backups),
        "STUB_LOG": str(log),
        "ENGLEKS_LOCK_TIMEOUT": "1",
    }
    s = Server(root=root, backups=backups, log=log, base_env=base_env)
    s.write_env(DEFAULT_ENV)
    return s


@pytest.fixture
def held_lock(server: Server) -> Iterator[None]:
    """Holds the deploy lock, as a running deploy would."""
    holder = subprocess.Popen(["flock", str(server.lock), "sleep", "5"])
    try:
        # Wait until the holder has the lock: a non-blocking flock then fails.
        for _ in range(100):
            probe = subprocess.run(["flock", "-n", str(server.lock), "true"], check=False)
            if probe.returncode != 0:
                break
            subprocess.run(["sleep", "0.05"], check=True)
        else:
            pytest.fail("lock holder never took the lock")
        yield
    finally:
        holder.kill()
        holder.wait()


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)

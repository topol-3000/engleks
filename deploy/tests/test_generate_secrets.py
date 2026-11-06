"""generate-secrets.sh prints the owner's generated secrets as dotenv lines."""

import os
import re
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "infisical" / "generate-secrets.sh"
SK_NAMES = {"LITELLM_MASTER_KEY", "API_LLM_API_KEY"}
NAMES = {
    "API_DB_PASSWORD",
    "LITELLM_DB_PASSWORD",
    "BOT_SERVICE_TOKEN",
    "LITELLM_UI_PASSWORD",
    "RESTIC_PASSWORD",
} | SK_NAMES


def generate() -> tuple[list[str], str]:
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines(), r.stderr


def parse(lines: list[str]) -> dict[str, str]:
    return dict(line.split("=", 1) for line in lines)


def test_prints_each_name_once_with_the_right_format():
    lines, _ = generate()
    assert len(lines) == 7
    values = parse(lines)
    assert set(values) == NAMES
    for name, value in values.items():
        pattern = r"sk-[0-9a-f]{64}" if name in SK_NAMES else r"[0-9a-f]{64}"
        assert re.fullmatch(pattern, value), name


def test_values_are_distinct():
    lines, _ = generate()
    values = list(parse(lines).values())
    assert len(set(values)) == 7


def test_second_run_gives_different_values():
    first, _ = generate()
    second, _ = generate()
    assert set(parse(first).values()).isdisjoint(parse(second).values())


def test_guidance_goes_to_stderr_not_stdout():
    lines, stderr = generate()
    assert all(re.fullmatch(r"[A-Z_]+=\S+", line) for line in lines)
    assert "RESTIC_PASSWORD" in stderr
    assert not any(v in stderr for v in parse(lines).values())


def test_openssl_failure_prints_nothing_and_fails(tmp_path: Path):
    stub = tmp_path / "openssl"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin"}
    bash = shutil.which("bash")
    assert bash
    r = subprocess.run([bash, str(SCRIPT)], capture_output=True, text=True, env=env, check=False)
    assert r.returncode != 0
    assert r.stdout == ""

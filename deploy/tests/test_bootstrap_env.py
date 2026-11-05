import re

import pytest

from .conftest import DEFAULT_ENV, NEW, Server, mode

# Telegram token, OpenAI key, healthchecks URL, Anthropic key (empty: optional), then the
# five values with their defaults.
ANSWERS = "123:tg\nsk-openai-x\nhttps://hc/x\n\n\n\n\n\n\n"

KEYS = {
    "API_DB_PASSWORD",
    "LITELLM_DB_PASSWORD",
    "BOT_SERVICE_TOKEN",
    "LITELLM_MASTER_KEY",
    "LITELLM_UI_USERNAME",
    "LITELLM_UI_PASSWORD",
    "RESTIC_PASSWORD",
    "BOT_TELEGRAM_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "HEALTHCHECKS_BACKUP_URL",
    "RESTIC_REPOSITORY",
    "LLM_UPSTREAM_MODEL",
    "LLM_UPSTREAM_API_BASE",
    "API_LLM_MODEL",
    "API_BOT_USERNAME",
    "API_RATE_LIMIT_PER_MINUTE",
    "API_DAILY_TOKEN_BUDGET",
    "COMPOSE_FILE",
    "ENGLEKS_TAG",
    "API_LLM_API_KEY",
}
GENERATED = [
    "API_DB_PASSWORD",
    "LITELLM_DB_PASSWORD",
    "BOT_SERVICE_TOKEN",
    "LITELLM_MASTER_KEY",
    "LITELLM_UI_PASSWORD",
    "RESTIC_PASSWORD",
]


@pytest.fixture
def fresh(server: Server) -> Server:
    server.env_file.unlink()
    return server


def bootstrap(server: Server, answers: str = ANSWERS):
    return server.run("bootstrap-env.sh", input=answers, STUB_HEAD=NEW)


def env_lines(server: Server) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in server.env_text().splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs.append((key, value))
    return pairs


def env_dict(server: Server) -> dict[str, str]:
    return dict(env_lines(server))


def test_refuses_when_env_exists(server: Server):
    r = bootstrap(server)
    assert r.returncode == 1
    assert server.env_text() == DEFAULT_ENV
    assert server.calls() == []


def test_writes_env(fresh: Server):
    r = bootstrap(fresh)
    assert r.returncode == 0, r.stderr
    assert mode(fresh.env_file) == 0o600
    keys = [k for k, _ in env_lines(fresh)]
    assert sorted(keys) == sorted(KEYS)
    lines = fresh.env_text().splitlines()
    for line in [
        f"ENGLEKS_TAG={NEW}",
        "COMPOSE_FILE=compose.yaml:compose.prod.yaml",
        "API_LLM_MODEL=gpt-6.1-terra",
        "RESTIC_REPOSITORY=sftp:storagebox:engleks",
        "LITELLM_UI_USERNAME=admin",
        "OPENAI_API_KEY=sk-openai-x",
        "API_RATE_LIMIT_PER_MINUTE=10",
        "API_DAILY_TOKEN_BUDGET=200000",
        "API_LLM_API_KEY=sk-generated",
        "BOT_TELEGRAM_TOKEN=123:tg",
        "ANTHROPIC_API_KEY=unused",
        "HEALTHCHECKS_BACKUP_URL=https://hc/x",
        "LLM_UPSTREAM_MODEL=openai/gpt-6.1-terra",
        "LLM_UPSTREAM_API_BASE=https://api.openai.com/v1",
        "API_BOT_USERNAME=",
    ]:
        assert line in lines


def test_generated_values(fresh: Server):
    r = bootstrap(fresh)
    assert r.returncode == 0, r.stderr
    env = env_dict(fresh)
    assert re.fullmatch(r"[A-Za-z0-9]{32,}", env["LITELLM_DB_PASSWORD"])
    assert env["LITELLM_MASTER_KEY"].startswith("sk-")
    values = [env[k] for k in GENERATED]
    assert all(len(v) >= 32 for v in values)
    assert len(set(values)) == len(values)


def test_creates_capped_key_and_restic_repo(fresh: Server):
    r = bootstrap(fresh)
    assert r.returncode == 0, r.stderr
    calls = fresh.calls()
    up = next(i for i, c in enumerate(calls) if c.startswith("docker compose up -d --wait "))
    assert calls[up].startswith("docker compose up -d --wait postgres litellm ")
    key = next(i for i, c in enumerate(calls) if "generate_key.py" in c)
    assert "generate_key.py --max-budget 10 --budget-duration 30d" in calls[key]
    init = next(i for i, c in enumerate(calls) if c.startswith("restic init"))
    assert up < key < init


def test_secrets_not_echoed(fresh: Server):
    r = bootstrap(fresh)
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    restic_password = env_dict(fresh)["RESTIC_PASSWORD"]
    for secret in ("123:tg", "sk-openai-x", restic_password, "sk-generated"):
        assert secret not in out
    assert not any(restic_password in c for c in fresh.calls())
    assert "password manager" in out


@pytest.mark.parametrize("empty", [0, 1, 2])
def test_empty_secret_aborts(fresh: Server, empty: int):
    answers = ANSWERS.split("\n")
    answers[empty] = ""
    r = bootstrap(fresh, "\n".join(answers))
    assert r.returncode == 1
    assert not fresh.env_file.exists()
    assert fresh.calls() == []


def test_anthropic_key_kept_when_given(fresh: Server):
    answers = ANSWERS.split("\n")
    answers[3] = "sk-ant-x"
    r = bootstrap(fresh, "\n".join(answers))
    assert r.returncode == 0, r.stderr
    assert env_dict(fresh)["ANTHROPIC_API_KEY"] == "sk-ant-x"
    assert "sk-ant-x" not in r.stdout + r.stderr


def test_failure_after_env_written_prints_recovery(fresh: Server):
    r = fresh.run("bootstrap-env.sh", input=ANSWERS, STUB_HEAD=NEW, STUB_FAIL="^restic init")
    assert r.returncode == 1
    # .env is deploy's and 0600, so the cleanup must run as deploy, not as the operator.
    assert (
        f"sudo -iu deploy sh -c 'cd {fresh.root} && docker compose down -v && rm .env'"
        in r.stdout + r.stderr
    )


@pytest.mark.parametrize("token", ["12$3:tg", "123 tg", "123#tg", '123"tg'])
def test_rejects_values_compose_would_misread(fresh: Server, token: str):
    r = bootstrap(fresh, ANSWERS.replace("123:tg", token, 1))
    assert r.returncode == 1
    assert not fresh.env_file.exists()
    assert token not in r.stdout + r.stderr

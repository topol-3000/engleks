"""The committed production config: deploy/prod.env, deploy/secrets.txt and compose.yaml agree."""

import re
from pathlib import Path

import yaml

from .conftest import SCRIPTS

REPO = SCRIPTS.parents[1]
# Today's value rule, as in deploy/server/engleks-render-env.
BAD_VALUE = re.compile(r"[\s$\"'`\\#]")


def secret_names() -> list[str]:
    lines = (REPO / "deploy/secrets.txt").read_text().splitlines()
    names = [line.split("#")[0].strip() for line in lines]
    return [n for n in names if n]


def prod_env() -> list[tuple[str, str]]:
    lines = (REPO / "deploy/prod.env").read_text().splitlines()
    pairs = [line.split("=", 1) for line in lines if line.strip() and not line.startswith("#")]
    return [(k, v) for k, v in pairs]


def compose_secret_variables() -> set[str]:
    compose = yaml.safe_load(Path(REPO / "compose.yaml").read_text())
    return {s["environment"] for s in compose["secrets"].values()}


def test_every_compose_secret_has_a_source() -> None:
    sources = set(secret_names()) | {k for k, _ in prod_env()}
    assert compose_secret_variables() - sources == set()


def test_sources_disjoint_and_unique() -> None:
    names = secret_names()
    config = [k for k, _ in prod_env()]
    assert len(names) == len(set(names))
    assert len(config) == len(set(config))
    assert set(names) & set(config) == set()


def test_real_files_pass_the_value_rule() -> None:
    for key, value in prod_env():
        assert not BAD_VALUE.search(value), key

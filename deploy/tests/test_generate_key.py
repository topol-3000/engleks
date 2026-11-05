import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "litellm" / "generate_key.py"
_spec = importlib.util.spec_from_file_location("generate_key", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gk)

MODELS = ["engleks-rewrite", "claude-sonnet-5-5", "claude-haiku-5-5", "mistral-small"]


def test_payload_without_budget_is_unchanged():
    assert gk.build_payload(None, None) == {
        "models": MODELS,
        "metadata": {"service": "engleks-api"},
    }


def test_payload_with_budget():
    p = gk.build_payload(10.0, "30d")
    assert p["max_budget"] == 10.0
    assert p["budget_duration"] == "30d"
    assert p["models"] == MODELS


@pytest.mark.parametrize("argv", [["--max-budget", "10"], ["--budget-duration", "30d"]])
def test_budget_flags_need_each_other(argv: list[str]):
    with pytest.raises(SystemExit) as e:
        gk.parse_args(argv)
    assert e.value.code == 2


@pytest.mark.parametrize("value", ["0", "-1", "ten"])
def test_budget_must_be_positive_number(value: str):
    with pytest.raises(SystemExit) as e:
        gk.parse_args(["--max-budget", value, "--budget-duration", "30d"])
    assert e.value.code == 2


def test_budget_flags_parsed():
    a = gk.parse_args(["--max-budget", "10", "--budget-duration", "30d"])
    assert (a.max_budget, a.budget_duration) == (10.0, "30d")


def test_no_flags():
    a = gk.parse_args([])
    assert (a.max_budget, a.budget_duration) == (None, None)

import importlib.util
import io
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "generate_key.py"
_spec = importlib.util.spec_from_file_location("generate_key", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gk)

MODELS = [
    "engleks-rewrite",
    "gpt-6.1-terra",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "mistral-small",
]


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


_ALL_CALLS: list[tuple[str, dict[str, object]]] = []


def fake(statuses: dict[str, int]):
    calls: list[tuple[str, dict[str, object]]] = []

    def post(path: str, payload: dict[str, object]) -> int:
        calls.append((path, payload))
        _ALL_CALLS.append((path, payload))
        return statuses[path]

    return post, calls


def test_ensure_updates_existing_key_models_only():
    post, calls = fake({"/key/update": 200})
    assert gk.ensure_key(post, "sk-x", 10.0, "30d") == "updated"
    assert calls == [("/key/update", {"key": "sk-x", "models": MODELS})]


def test_ensure_creates_missing_key_with_value_and_budget():
    post, calls = fake({"/key/update": 404, "/key/generate": 200})
    assert gk.ensure_key(post, "sk-x", 10.0, "30d") == "created"
    assert calls[1] == (
        "/key/generate",
        {
            "key": "sk-x",
            "models": MODELS,
            "metadata": {"service": "engleks-api"},
            "max_budget": 10.0,
            "budget_duration": "30d",
        },
    )


def test_ensure_creates_missing_key_without_budget():
    post, calls = fake({"/key/update": 404, "/key/generate": 200})
    assert gk.ensure_key(post, "sk-x", None, None) == "created"
    assert calls[1] == (
        "/key/generate",
        {"key": "sk-x", "models": MODELS, "metadata": {"service": "engleks-api"}},
    )


@pytest.mark.parametrize(
    "statuses", [{"/key/update": 500}, {"/key/update": 404, "/key/generate": 400}]
)
def test_ensure_fails_on_other_status_without_printing_key(
    statuses: dict[str, int], capsys: pytest.CaptureFixture[str]
):
    post, _ = fake(statuses)
    with pytest.raises(SystemExit) as e:
        gk.ensure_key(post, "sk-x", 10.0, "30d")
    out = capsys.readouterr()
    assert "sk-x" not in str(e.value) and "sk-x" not in out.out + out.err
    assert e.value.code != 0


@pytest.mark.parametrize(("status", "result"), [(200, "deleted"), (404, "missing")])
def test_delete(status: int, result: str):
    post, calls = fake({"/key/delete": status})
    assert gk.delete_key(post, "sk-x") == result
    assert calls == [("/key/delete", {"keys": ["sk-x"]})]


def test_delete_fails_on_other_status(capsys: pytest.CaptureFixture[str]):
    post, _ = fake({"/key/delete": 500})
    with pytest.raises(SystemExit) as e:
        gk.delete_key(post, "sk-x")
    out = capsys.readouterr()
    assert "sk-x" not in str(e.value) and "sk-x" not in out.out + out.err
    assert e.value.code != 0


def test_key_never_in_path():
    assert _ALL_CALLS
    assert all("sk-x" not in path for path, _ in _ALL_CALLS)


def test_ensure_and_delete_are_exclusive():
    with pytest.raises(SystemExit) as e:
        gk.parse_args(["--ensure-stdin", "--delete-stdin"])
    assert e.value.code == 2


def test_ensure_keeps_budget_pairing_rule():
    with pytest.raises(SystemExit) as e:
        gk.parse_args(["--ensure-stdin", "--max-budget", "10"])
    assert e.value.code == 2


def test_stdin_flags_parsed():
    assert gk.parse_args(["--ensure-stdin"]).ensure_stdin is True
    assert gk.parse_args(["--delete-stdin"]).delete_stdin is True
    a = gk.parse_args([])
    assert (a.ensure_stdin, a.delete_stdin) == (False, False)


@pytest.mark.parametrize("flag", ["--ensure-stdin", "--delete-stdin"])
def test_empty_stdin_key_exits_with_error(
    flag: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    master = tmp_path / "master"
    master.write_text("sk-master")
    monkeypatch.setattr(gk, "Path", lambda _p: master)
    monkeypatch.setattr(gk.sys, "stdin", io.StringIO("  \n"))
    monkeypatch.setattr(gk, "make_post", lambda _k: pytest.fail("must not call the proxy"))
    with pytest.raises(SystemExit) as e:
        gk.main([flag])
    assert e.value.code not in (0, None)

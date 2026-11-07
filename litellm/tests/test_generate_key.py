import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Self

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


class _Response(io.BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    path = tmp_path / "litellm_master_key"
    path.write_text("sk-master\n")
    monkeypatch.setattr(gk, "MASTER_KEY_FILE", path)
    return "sk-master"


def test_payload_restricts_the_key_to_the_models():
    assert gk.build_payload() == {
        "models": MODELS,
        "metadata": {"service": "engleks-api"},
    }


def test_prints_the_generated_key(
    master_key: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    requests: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        requests.append(request)
        return _Response(json.dumps({"key": "sk-generated"}).encode())

    monkeypatch.setattr(gk.urllib.request, "urlopen", urlopen)
    gk.main()

    assert capsys.readouterr().out == "API_LLM_API_KEY=sk-generated\n"
    [request] = requests
    assert request.full_url == "http://localhost:4000/key/generate"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {master_key}"
    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == gk.build_payload()


def test_proxy_error_exits_non_zero_with_the_status(
    master_key: str, monkeypatch: pytest.MonkeyPatch
):
    def urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(gk.urllib.request, "urlopen", urlopen)
    with pytest.raises(SystemExit) as e:
        gk.main()

    assert e.value.code not in (0, None)
    assert "401" in str(e.value.code)

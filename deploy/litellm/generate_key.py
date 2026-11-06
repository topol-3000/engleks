"""Create the API's virtual key, restricted to the models in config.yaml.

Run inside the litellm container:

    python /deploy/generate_key.py
    python /deploy/generate_key.py --max-budget 10 --budget-duration 30d
    printf %s "$KEY" | python /deploy/generate_key.py --ensure-stdin --max-budget 10 \\
        --budget-duration 30d
    printf %s "$KEY" | python /deploy/generate_key.py --delete-stdin

The budget flags go together: a cap without a duration would never reset. When the cap is
reached, LiteLLM rejects the key's calls until the period ends.

--ensure-stdin takes the key's value from stdin (never from argv or a URL) and makes the proxy
hold it: an existing key gets its model list updated, a missing one is created with the budget.
It prints "updated" or "created". --delete-stdin removes a key and prints "deleted" or
"missing". Both exit non-zero on any other HTTP status, with a message that omits the key.

Standard library only: the image has no curl.
"""

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

PROXY_URL = "http://localhost:4000"
# HTTP statuses the proxy answers for a key it does not hold.
MISSING = {404}

MODELS = [
    "engleks-rewrite",
    "gpt-6.1-terra",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "mistral-small",
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the API's LiteLLM virtual key.")
    parser.add_argument("--max-budget", type=str, default=None, help="spending cap in USD")
    parser.add_argument(
        "--budget-duration", default=None, help="period after which the spend resets, e.g. 30d"
    )
    stdin_mode = parser.add_mutually_exclusive_group()
    stdin_mode.add_argument(
        "--ensure-stdin",
        action="store_true",
        help="read a key value on stdin; update it if the proxy has it, else create it",
    )
    stdin_mode.add_argument(
        "--delete-stdin", action="store_true", help="read a key value on stdin and delete it"
    )
    args = parser.parse_args(argv)
    if (args.max_budget is None) != (args.budget_duration is None):
        parser.error("--max-budget and --budget-duration must be given together")
    if args.max_budget is not None:
        try:
            args.max_budget = float(args.max_budget)
        except ValueError:
            parser.error(f"--max-budget must be a number, got {args.max_budget!r}")
        if not (math.isfinite(args.max_budget) and args.max_budget > 0):
            parser.error("--max-budget must be a positive number")
    return args


def build_payload(max_budget: float | None, budget_duration: str | None) -> dict[str, object]:
    payload: dict[str, object] = {"models": MODELS, "metadata": {"service": "engleks-api"}}
    if max_budget is not None:
        payload["max_budget"] = max_budget
        payload["budget_duration"] = budget_duration
    return payload


def ensure_key(
    post: Callable[[str, dict[str, object]], int],
    key: str,
    max_budget: float | None,
    budget_duration: str | None,
) -> str:
    status = post("/key/update", {"key": key, "models": MODELS})
    if status == 200:
        return "updated"
    if status not in MISSING:
        sys.exit(f"key update failed: HTTP {status}")
    status = post("/key/generate", {"key": key, **build_payload(max_budget, budget_duration)})
    if status != 200:
        sys.exit(f"key creation failed: HTTP {status}")
    return "created"


def delete_key(post: Callable[[str, dict[str, object]], int], key: str) -> str:
    status = post("/key/delete", {"keys": [key]})
    if status == 200:
        return "deleted"
    if status in MISSING:
        return "missing"
    sys.exit(f"key deletion failed: HTTP {status}")


def make_post(master_key: str) -> Callable[[str, dict[str, object]], int]:
    def post(path: str, payload: dict[str, object]) -> int:
        request = urllib.request.Request(
            f"{PROXY_URL}{path}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    return post


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    master_key = Path("/run/secrets/litellm_master_key").read_text().strip()
    if args.ensure_stdin or args.delete_stdin:
        key = sys.stdin.read().strip()
        if not key:
            sys.exit("no key on stdin")
        post = make_post(master_key)
        if args.ensure_stdin:
            print(ensure_key(post, key, args.max_budget, args.budget_duration))
        else:
            print(delete_key(post, key))
        return
    request = urllib.request.Request(
        f"{PROXY_URL}/key/generate",
        data=json.dumps(build_payload(args.max_budget, args.budget_duration)).encode(),
        headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            key = json.load(response)["key"]
    except urllib.error.HTTPError as exc:
        sys.exit(f"key generation failed: HTTP {exc.code}")
    print(f"API_LLM_API_KEY={key}")


if __name__ == "__main__":
    main(sys.argv[1:])

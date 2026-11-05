"""Create the API's virtual key, restricted to the models in config.yaml.

Run inside the litellm container:

    python /deploy/generate_key.py
    python /deploy/generate_key.py --max-budget 10 --budget-duration 30d

The budget flags go together: a cap without a duration would never reset. When the cap is
reached, LiteLLM rejects the key's calls until the period ends.

Standard library only: the image has no curl.
"""

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

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


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    master_key = Path("/run/secrets/litellm_master_key").read_text().strip()
    request = urllib.request.Request(
        "http://localhost:4000/key/generate",
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

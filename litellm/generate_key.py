"""Create the API's virtual key, restricted to the models in config.yaml.

Run inside the litellm container (`make llm-key`):

    python /litellm/generate_key.py

It prints API_LLM_API_KEY=<key>, the line for .env. Standard library only: the image has
no curl.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROXY_URL = "http://localhost:4000"
MASTER_KEY_FILE = Path("/run/secrets/litellm_master_key")

MODELS = [
    "engleks-rewrite",
    "gpt-6.1-terra",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "mistral-small",
]


def build_payload() -> dict[str, object]:
    return {"models": MODELS, "metadata": {"service": "engleks-api"}}


def main() -> None:
    master_key = MASTER_KEY_FILE.read_text().strip()
    request = urllib.request.Request(
        f"{PROXY_URL}/key/generate",
        data=json.dumps(build_payload()).encode(),
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
    main()

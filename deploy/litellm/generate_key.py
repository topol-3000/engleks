"""Create the API's virtual key, restricted to the models in config.yaml.

Run inside the litellm container.

Standard library only: the image has no curl.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

master_key = Path("/run/secrets/litellm_master_key").read_text().strip()
models = ["engleks-rewrite", "claude-sonnet-5-5", "claude-haiku-5-5", "gemma4-12b", "qwen3.8-27b"]
request = urllib.request.Request(
    "http://localhost:4000/key/generate",
    data=json.dumps({"models": models, "metadata": {"service": "engleks-api"}}).encode(),
    headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        key = json.load(response)["key"]
except urllib.error.HTTPError as exc:
    sys.exit(f"key generation failed: HTTP {exc.code}")
print(f"API_LLM_API_KEY={key}")

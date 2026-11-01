#!/bin/sh
# LiteLLM reads keys from env vars, not files: export the Compose secrets, then exec it.
set -eu
master_key=$(cat /run/secrets/litellm_master_key)
ui_username=$(cat /run/secrets/litellm_ui_username)
ui_password=$(cat /run/secrets/litellm_ui_password)
openai_api_key=$(cat /run/secrets/openai_api_key)
db_password=$(cat /run/secrets/litellm_db_password)
export LITELLM_MASTER_KEY="$master_key"
export UI_USERNAME="$ui_username"
export UI_PASSWORD="$ui_password"
export OPENAI_API_KEY="$openai_api_key"
export DATABASE_URL="postgresql://litellm:${db_password}@postgres:5432/litellm"
exec litellm --config /deploy/config.yaml --port 4000

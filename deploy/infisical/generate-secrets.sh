#!/usr/bin/env bash
# Prints the secrets that engleks generates itself, as dotenv lines, for Infisical's
# ".env import" (project engleks, environment prod). Run it on the owner's machine:
#
#   deploy/infisical/generate-secrets.sh
#
# stdout is exactly seven KEY=value lines; the guidance goes to stderr. Each value is
# `openssl rand -hex 32`: letters and digits only, so it is safe in a URL and passes the
# value rule of engleks-render-env. See docs/deployment.md.
set -euo pipefail

cat >&2 <<'EOF2'
Paste the lines below into Infisical (project engleks, environment prod, ".env import").
Save RESTIC_PASSWORD in your password manager now: the backups cannot be read without it.
Add BOT_TELEGRAM_TOKEN, OPENAI_API_KEY, ANTHROPIC_API_KEY (or "unused") and
HEALTHCHECKS_BACKUP_URL by hand.
EOF2

random_hex() {
  openssl rand -hex 32
}

# Plain assignments, so a failing openssl aborts the script (set -e) before anything is
# printed: no partial set of secrets can reach stdout.
api_db_password=$(random_hex)
litellm_db_password=$(random_hex)
bot_service_token=$(random_hex)
litellm_master_key=sk-$(random_hex)
litellm_ui_password=$(random_hex)
api_llm_api_key=sk-$(random_hex)
restic_password=$(random_hex)

printf '%s\n' \
  "API_DB_PASSWORD=$api_db_password" \
  "LITELLM_DB_PASSWORD=$litellm_db_password" \
  "BOT_SERVICE_TOKEN=$bot_service_token" \
  "LITELLM_MASTER_KEY=$litellm_master_key" \
  "LITELLM_UI_PASSWORD=$litellm_ui_password" \
  "API_LLM_API_KEY=$api_llm_api_key" \
  "RESTIC_PASSWORD=$restic_password"

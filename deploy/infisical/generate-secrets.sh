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

echo "API_DB_PASSWORD=$(random_hex)"
echo "LITELLM_DB_PASSWORD=$(random_hex)"
echo "BOT_SERVICE_TOKEN=$(random_hex)"
echo "LITELLM_MASTER_KEY=sk-$(random_hex)"
echo "LITELLM_UI_PASSWORD=$(random_hex)"
echo "API_LLM_API_KEY=sk-$(random_hex)"
echo "RESTIC_PASSWORD=$(random_hex)"

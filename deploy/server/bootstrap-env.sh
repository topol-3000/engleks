#!/usr/bin/env bash
# Creates the production .env once: generates the passwords and keys, asks for the
# third-party secrets, creates the API's LiteLLM key with the global spending cap and
# initialises the restic repository on the Storage Box.
#
# Run once as `deploy` after setup.sh:  sudo -iu deploy /opt/engleks/deploy/server/bootstrap-env.sh
# It refuses to run if .env exists. See docs/deployment.md.
#
# ENGLEKS_DIR exists for the tests.
set -euo pipefail

ENGLEKS_DIR=${ENGLEKS_DIR:-/opt/engleks}
ENV_FILE=$ENGLEKS_DIR/.env

umask 077
cd "$ENGLEKS_DIR"

if [[ -e $ENV_FILE ]]; then
  echo "error: $ENV_FILE already exists; this script only creates a new one" >&2
  exit 1
fi

# Values go into .env unquoted, where Compose would interpolate $ and treat # and quotes
# specially, so those are refused rather than escaped.
check_value() {
  if [[ $2 =~ [[:space:]\$\"\'\`\\#] ]]; then
    echo "error: $1 must not contain spaces, quotes, \$, # or backslashes" >&2
    exit 1
  fi
}

# Reads a secret without echoing it. An empty answer aborts.
ask_secret() {
  local var=$1 value=""
  IFS= read -rsp "$var: " value || true
  if [[ -t 0 ]]; then echo >&2; fi
  if [[ -z $value ]]; then
    echo "error: $var is required" >&2
    exit 1
  fi
  check_value "$var" "$value"
  printf -v "$var" '%s' "$value"
}

# Reads a secret without echoing it. An empty answer stores "unused": Compose still needs
# the variable set for the secret.
ask_optional_secret() {
  local var=$1 value=""
  IFS= read -rsp "$var (Enter to skip): " value || true
  if [[ -t 0 ]]; then echo >&2; fi
  value=${value:-unused}
  check_value "$var" "$value"
  printf -v "$var" '%s' "$value"
}

# Reads a non-secret value; an empty answer takes the default.
ask_value() {
  local var=$1 default=$2 value=""
  IFS= read -rp "$var [$default]: " value || true
  value=${value:-$default}
  check_value "$var" "$value"
  printf -v "$var" '%s' "$value"
}

random_hex() {
  openssl rand -hex 32
}

ask_secret BOT_TELEGRAM_TOKEN
ask_secret OPENAI_API_KEY
ask_secret HEALTHCHECKS_BACKUP_URL
# Only for the claude-* aliases in deploy/litellm/config.yaml.
ask_optional_secret ANTHROPIC_API_KEY
# Relative to the Storage Box home directory: the SFTP root (/) is not writable there.
ask_value RESTIC_REPOSITORY sftp:storagebox:engleks
ask_value LLM_UPSTREAM_MODEL openai/gpt-6.1-terra
ask_value LLM_UPSTREAM_API_BASE https://api.openai.com/v1
ask_value API_LLM_MODEL gpt-6.1-terra
ask_value API_BOT_USERNAME ""

tag=$(git rev-parse HEAD)
if [[ ! $tag =~ ^[0-9a-f]{40}$ ]]; then
  echo "error: could not read the checkout's commit SHA" >&2
  exit 1
fi

RESTIC_PASSWORD=$(random_hex)

on_exit() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    cat >&2 <<EOF

error: bootstrap failed after $ENV_FILE was written. To start over, as yourself:
  sudo -iu deploy sh -c 'cd $ENGLEKS_DIR && docker compose down -v && rm .env'
then run this script again.
EOF
  fi
}

# API_LLM_API_KEY is a placeholder until the key exists: Compose needs every secret's
# variable set before it starts anything.
cat >"$ENV_FILE" <<EOF
# Production .env, written by deploy/server/bootstrap-env.sh. Keep a copy in a password
# manager: the backups contain this file and need RESTIC_PASSWORD to be read.
COMPOSE_FILE=compose.yaml:compose.prod.yaml
ENGLEKS_TAG=$tag

API_DB_PASSWORD=$(random_hex)
BOT_SERVICE_TOKEN=$(random_hex)
BOT_TELEGRAM_TOKEN=$BOT_TELEGRAM_TOKEN
API_LLM_API_KEY=pending
API_LLM_MODEL=$API_LLM_MODEL
API_BOT_USERNAME=$API_BOT_USERNAME
API_RATE_LIMIT_PER_MINUTE=10
API_DAILY_TOKEN_BUDGET=200000

LLM_UPSTREAM_MODEL=$LLM_UPSTREAM_MODEL
LLM_UPSTREAM_API_BASE=$LLM_UPSTREAM_API_BASE
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
OPENAI_API_KEY=$OPENAI_API_KEY
LITELLM_MASTER_KEY=sk-$(random_hex)
LITELLM_DB_PASSWORD=$(random_hex)
LITELLM_UI_USERNAME=admin
LITELLM_UI_PASSWORD=$(random_hex)

RESTIC_REPOSITORY=$RESTIC_REPOSITORY
RESTIC_PASSWORD=$RESTIC_PASSWORD
HEALTHCHECKS_BACKUP_URL=$HEALTHCHECKS_BACKUP_URL
EOF
trap on_exit EXIT

echo "==> starting postgres and litellm"
docker compose up -d --wait postgres litellm

echo "==> creating the API's LiteLLM key (cap: \$10 per 30 days)"
key=$(docker compose exec -T litellm python /deploy/generate_key.py \
  --max-budget 10 --budget-duration 30d | tr -d '\r' | sed -n 's/^API_LLM_API_KEY=//p')
if [[ -z $key ]]; then
  echo "error: generate_key.py printed no key" >&2
  exit 1
fi
tmp=$(mktemp "$ENV_FILE.XXXXXX")
awk -v line="API_LLM_API_KEY=$key" '/^API_LLM_API_KEY=/ { print line; next } { print }' \
  "$ENV_FILE" >"$tmp"
mv -f "$tmp" "$ENV_FILE"

echo "==> initialising the restic repository"
RESTIC_REPOSITORY=$RESTIC_REPOSITORY RESTIC_PASSWORD=$RESTIC_PASSWORD restic init

cat <<EOF

Done. $ENV_FILE is written.

Save a copy of $ENV_FILE in your password manager now. The nightly backups include it,
and without RESTIC_PASSWORD from that copy they cannot be decrypted.

Next: create the GitHub Environment "production" and deploy main's head with
"Run workflow" (docs/deployment.md).
EOF

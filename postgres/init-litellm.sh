#!/bin/sh
# Runs once, on an empty data directory: the LiteLLM proxy gets its own role and
# database, and cannot read ours. Values go in as psql variables, never via the shell.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v pw="$(cat /run/secrets/litellm_db_password)" -v appdb="$POSTGRES_DB" <<'SQL'
CREATE ROLE litellm LOGIN PASSWORD :'pw';
CREATE DATABASE litellm OWNER litellm;
REVOKE ALL ON DATABASE :"appdb" FROM PUBLIC;
SQL

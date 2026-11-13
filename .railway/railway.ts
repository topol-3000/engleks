// The Railway environment for engleks: managed Postgres, the api and the bot.
// CI plans a change on the PR and applies it on merge; see docs/deployment/railway.md.
// "${{service.VAR}}" strings are Railway templates resolved at deploy time. Keep them in
// double quotes: inside backticks, ${...} would be a JavaScript interpolation.
import { defineRailway, github, postgres, preserve, project, service } from "railway/iac";

const REGION = "europe-west4-drams3a";
const source = github("topol-3000/engleks", { branch: "main", checkSuites: true });
// Files both images are built from: a change here rebuilds both services.
const shared = ["pyproject.toml", "uv.lock", "packages/contracts/**"];

export default defineRailway(() => {
  const db = postgres("postgres", { region: REGION });

  const api = service("api", {
    source,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "apps/api/Dockerfile",
      watchPatterns: ["apps/api/**", "apps/bot/pyproject.toml", ...shared],
    },
    regions: { [REGION]: 1 },
    preDeploy: "alembic upgrade head",
    healthcheck: "/readyz",
    healthcheckTimeout: 120,
    deploy: { restartPolicyType: "ON_FAILURE" },
    env: {
      API_DB_CONNECTION_STRING:
        "postgresql+asyncpg://${{postgres.PGUSER}}:${{postgres.PGPASSWORD}}" +
        "@${{postgres.PGHOST}}:${{postgres.PGPORT}}/${{postgres.PGDATABASE}}",
      API_SERVICE_TOKEN: preserve(),
      API_LLM_API_KEY: preserve(),
      API_LLM_BASE_URL: preserve(),
      API_LLM_MODEL: "engleks-rewrite",
      API_RATE_LIMIT_PER_MINUTE: "10",
      API_DAILY_TOKEN_BUDGET: "200000",
      // The production bot's username without "@", set in the Railway UI next to BOT_TELEGRAM_TOKEN.
      API_BOT_USERNAME: preserve(),
      API_LOG_FORMAT: "json",
      API_LOG_LEVEL: "INFO",
      PORT: "8000",
    },
  });

  // One poller per token: a second replica or an overlapping deploy gets 409s from Telegram.
  const bot = service("bot", {
    source,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "apps/bot/Dockerfile",
      watchPatterns: ["apps/bot/**", "apps/api/pyproject.toml", ...shared],
    },
    regions: { [REGION]: 1 },
    deploy: { restartPolicyType: "ALWAYS", overlapSeconds: 0 },
    env: {
      BOT_TELEGRAM_TOKEN: preserve(),
      BOT_SERVICE_TOKEN: api.env.API_SERVICE_TOKEN,
      BOT_API_BASE_URL: "http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000",
      BOT_LOG_FORMAT: "json",
      BOT_LOG_LEVEL: "INFO",
    },
  });

  return project("engleks", { resources: [db, api, bot] });
});

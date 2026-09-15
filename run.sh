#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt

echo "Starting PostgreSQL"
docker compose up -d

for attempt in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U sitepilot -d clinical_agent >/dev/null 2>&1; then
    echo "PostgreSQL is healthy"
    break
  fi
  if [[ "$attempt" -eq 60 ]]; then
    echo "PostgreSQL did not become healthy"
    docker compose logs postgres || true
    exit 1
  fi
  sleep 2
done

python3 - <<'PY'
from dotenv import load_dotenv
load_dotenv('.env')

from app.config import get_settings
from app.database import database_probe

settings = get_settings()
result = database_probe(settings.database_url)
if result < 1:
    raise SystemExit("Seeded database is empty")
print(f"Database probe found {result} studies")
PY

python3 - <<'PY'
from dotenv import load_dotenv
load_dotenv('.env')

from app.config import get_settings
from app.main import app
from app.model_client import RealModelClient
from app.prompts import PROMPT_VERSION

settings = get_settings()
client = RealModelClient(settings)
route_paths = {route.path for route in app.routes}
if "/health" not in route_paths:
    raise SystemExit("Health route is missing")
if not PROMPT_VERSION or client is None:
    raise SystemExit("Application components did not load")
print("Application imports and routes loaded")
PY

if [[ -n "${OPENAI_API_KEY:-}" || -f .env && -n "$(grep -E '^OPENAI_API_KEY=.+' .env 2>/dev/null || true)" ]]; then
  echo "Provider key found, running direct model ping"
  python3 - <<'PY'
import asyncio
from dotenv import load_dotenv
load_dotenv('.env')

from app.config import get_settings
from app.model_client import RealModelClient

async def main() -> None:
    client = RealModelClient(get_settings())
    await client.ping()

asyncio.run(main())
print("Model ping completed")
PY
else
  echo "No provider key found; skipping model ping"
fi

uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/clinical-agent-uvicorn.log 2>&1 &
server_pid=$!
cleanup_server() {
  kill "$server_pid" >/dev/null 2>&1 || true
  wait "$server_pid" >/dev/null 2>&1 || true
}
trap cleanup_server EXIT

for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
    echo "API health check passed"
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    cat /tmp/clinical-agent-uvicorn.log || true
    exit 1
  fi
  sleep 1
done

cleanup_server
trap - EXIT
echo "Clinical trial agent ready"

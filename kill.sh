#!/usr/bin/env bash
set +e

cd /root/task 2>/dev/null || true
docker compose down -v || true
docker network rm task_default 2>/dev/null || true
docker system prune --volumes -f || true
rm -rf /root/task || true

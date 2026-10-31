#!/usr/bin/env bash
# Copy the project to the Ubuntu server and (re)start it.
#   ./deploy.sh                          -> uses SERVER/PORT from deploy.env (not in git)
#   SERVER=me@host PORT=8090 ./deploy.sh -> another target
# deploy.env example:
#   SERVER=user@192.168.1.10
#   PORT=8090
# The server's ./data folder (the database) and the rest of its .env (tunnel token) are never overwritten.
set -euo pipefail
cd "$(dirname "$0")"
[ -z "${SERVER:-}" ] && [ -f deploy.env ] && . ./deploy.env
: "${SERVER:?Set SERVER=user@host (in deploy.env or the environment)}"
PORT="${PORT:-8080}"
DIR="${DIR:-fishalyzer}"
tar czf - --exclude=data --exclude=__pycache__ --exclude=.git --exclude=deploy.env --exclude=.env . | ssh "$SERVER" "mkdir -p $DIR && tar xzf - -C $DIR"
ssh "$SERVER" "cd $DIR && mkdir -p data && touch .env && { grep -v '^PORT=' .env || true; echo PORT=$PORT; } > .env.new && mv .env.new .env && chmod 600 .env && docker compose up -d --build && docker compose ps"
echo "Deployed. Open http://${SERVER#*@}:$PORT"

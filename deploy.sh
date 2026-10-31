#!/usr/bin/env bash
# Copy the project to the Ubuntu server and (re)start it.
#   ./deploy.sh                    -> goblinsv@goblinserver (192.168.3.12)
#   SERVER=me@host ./deploy.sh     -> another target
# The server's ./data folder (the database) is never overwritten.
set -euo pipefail
SERVER="${SERVER:-goblinsv@192.168.3.12}"
PORT="${PORT:-8090}"
DIR="${DIR:-fishalyzer}"
cd "$(dirname "$0")"
tar czf - --exclude=data --exclude=__pycache__ --exclude=.git . | ssh "$SERVER" "mkdir -p $DIR && tar xzf - -C $DIR"
ssh "$SERVER" "cd $DIR && mkdir -p data && PORT=$PORT docker compose up -d --build && docker compose ps"
echo "Deployed. Open http://${SERVER#*@}:$PORT"

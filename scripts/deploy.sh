#!/usr/bin/env bash
# Copies the app to the LXC and rebuilds it. Server-only files (.env, config/) are never overwritten.
set -euo pipefail
HOST=${HOST:-xiaomi-cam-hub-lxc}
DIR=/opt/xiaomi-cam-hub
cd "$(dirname "$0")/.."

tar czf - --exclude=__pycache__ Dockerfile requirements.txt alembic.ini docker-compose.yml Caddyfile.example app migrations scripts \
  | ssh "$HOST" "tar xzf - --no-same-owner -C $DIR"
ssh "$HOST" "cd $DIR && docker compose up -d --build --remove-orphans && docker compose ps"

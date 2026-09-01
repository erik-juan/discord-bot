#!/bin/bash
set -eu

APP_DIR="/home/ubuntu/discord_bot"
cd "$APP_DIR"

if [ ! -f krillion.json ]; then
  touch krillion.json
fi

docker compose down --remove-orphans || true
docker compose up --build -d --remove-orphans --force-recreate

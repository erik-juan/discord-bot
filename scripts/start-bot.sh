#!/bin/bash
set -eu

APP_DIR="/home/ubuntu/discord_bot"
cd "$APP_DIR"

# Docker bind-mounts a missing host path as a directory. Create the file first so
# /data/krillion.json stays a file across deploys.
if [ ! -f krillion.json ]; then
  touch krillion.json
fi

docker compose down --remove-orphans || true
docker compose up --build -d --remove-orphans --force-recreate

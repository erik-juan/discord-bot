#!/bin/bash
set -eu

APP_DIR="/home/ubuntu/discord_bot"
cd "$APP_DIR"

docker compose down --remove-orphans || true
docker compose up --build -d --remove-orphans --force-recreate

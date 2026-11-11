#!/usr/bin/env bash
# Установка на чистый VPS (Ubuntu/Debian): bash setup.sh
set -e
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Ставлю Docker…"
  curl -fsSL https://get.docker.com | sh
fi

if [ ! -f .env ]; then
  echo
  read -rp "Токен бота от @BotFather: " TOKEN
  read -rp "Пароль для сайта мастера: " PASS
  read -rp "Домен для HTTPS (Enter — без домена, по IP): " DOMAIN
  cat > .env <<ENV
BOT_TOKEN=$TOKEN
GM_PASSWORD=$PASS
WEB_PORT=8080
DOMAIN=$DOMAIN
ENV
  chmod 600 .env
fi

DOMAIN=$(grep -E '^DOMAIN=' .env | cut -d= -f2-)
WEB_PORT=$(grep -E '^WEB_PORT=' .env | cut -d= -f2-)
mkdir -p data
if [ -n "$DOMAIN" ]; then
  docker compose --profile https up -d --build
  echo
  echo "Готово! Сайт мастера: https://$DOMAIN"
else
  docker compose up -d --build
  IP=$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  echo
  echo "Готово! Сайт мастера: http://$IP:${WEB_PORT:-8080}"
fi
echo "Логи: docker compose logs -f dnd"

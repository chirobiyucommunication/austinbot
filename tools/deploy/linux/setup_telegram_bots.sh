#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
BOT_USER="${2:-${SUDO_USER:-$(id -un)}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE_PATH="${ENV_FILE_PATH:-/etc/pocket-option-bot/bot.env}"

if [ "$EUID" -ne 0 ]; then
  echo "Run as root: sudo bash tools/deploy/linux/setup_telegram_bots.sh <repo_root> <bot_user>"
  exit 1
fi

if [ ! -d "$REPO_ROOT" ]; then
  echo "Repository path not found: $REPO_ROOT"
  exit 1
fi

if ! id "$BOT_USER" >/dev/null 2>&1; then
  echo "Linux user not found: $BOT_USER"
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python not found: $PYTHON_BIN"
  exit 1
fi

apt-get update
apt-get install -y python3-venv python3-pip

su - "$BOT_USER" -c "cd '$REPO_ROOT' && $PYTHON_BIN -m venv .venv"
su - "$BOT_USER" -c "cd '$REPO_ROOT' && .venv/bin/pip install --upgrade pip"
su - "$BOT_USER" -c "cd '$REPO_ROOT' && .venv/bin/pip install -r requirements.txt"

mkdir -p /etc/pocket-option-bot
if [ ! -f "$ENV_FILE_PATH" ]; then
  install -m 600 -o "$BOT_USER" -g "$BOT_USER" "$REPO_ROOT/tools/deploy/linux/bot.env.example" "$ENV_FILE_PATH"
  echo "Created env file at $ENV_FILE_PATH"
  echo "Edit it now with real tokens and IDs before using the bots."
else
  chown "$BOT_USER":"$BOT_USER" "$ENV_FILE_PATH"
  chmod 600 "$ENV_FILE_PATH"
fi

ACTIVATION_UNIT_TMP="/tmp/pocket-option-activation-bot.service"
ADMIN_UNIT_TMP="/tmp/pocket-option-admin-bot.service"

sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__BOT_USER__|$BOT_USER|g" \
  "$REPO_ROOT/tools/deploy/linux/systemd/pocket-option-activation-bot.service" > "$ACTIVATION_UNIT_TMP"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__BOT_USER__|$BOT_USER|g" \
  "$REPO_ROOT/tools/deploy/linux/systemd/pocket-option-admin-bot.service" > "$ADMIN_UNIT_TMP"

install -m 644 "$ACTIVATION_UNIT_TMP" /etc/systemd/system/pocket-option-activation-bot.service
install -m 644 "$ADMIN_UNIT_TMP" /etc/systemd/system/pocket-option-admin-bot.service

systemctl daemon-reload
systemctl enable pocket-option-activation-bot.service
systemctl enable pocket-option-admin-bot.service
systemctl restart pocket-option-activation-bot.service
systemctl restart pocket-option-admin-bot.service

echo "Services installed and started."
echo "Check status: systemctl status pocket-option-activation-bot --no-pager"
echo "Check status: systemctl status pocket-option-admin-bot --no-pager"
echo "Logs: journalctl -u pocket-option-activation-bot -f"
echo "Logs: journalctl -u pocket-option-admin-bot -f"
#!/bin/sh
# Обновление на сервере (от root): sh /home/assistant/ai-assistant-bot/deploy/update.sh
set -e
cd /home/assistant/ai-assistant-bot
sudo -u assistant git pull
sudo -u assistant /usr/local/bin/uv sync --frozen --no-dev
systemctl restart assistant   # миграции применятся в ExecStartPre
systemctl --no-pager status assistant

# Деплой на VPS (Ubuntu/Debian): systemd + Caddy

| Что | Где |
|---|---|
| Пользователь | `assistant` (не root) |
| Код | `/home/assistant/ai-assistant-bot` |
| Настройки | `/home/assistant/ai-assistant-bot/.env` (права 600) |
| База | PostgreSQL, база `assistant`, расширение `vector` |
| Служба | `/etc/systemd/system/assistant.service` |

Альтернатива — Docker: `docker compose --profile app up -d --build`.

## Установка (от root)

```sh
apt update && apt install -y git curl postgresql redis-server
# pgvector для своей версии Postgres (узнать: psql --version), например 18:
apt install -y postgresql-18-pgvector
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

useradd --create-home --shell /bin/bash assistant
sudo -u postgres createuser assistant
sudo -u postgres createdb -O assistant assistant
# Расширение создаёт суперпользователь: у владельца базы на это обычно нет прав
sudo -u postgres psql assistant -c "CREATE EXTENSION IF NOT EXISTS vector"

sudo -u assistant git clone https://github.com/egorstep147/ai-assistant-bot.git /home/assistant/ai-assistant-bot
cd /home/assistant/ai-assistant-bot && sudo -u assistant /usr/local/bin/uv sync --frozen --no-dev
```

## .env
```sh
sudo -u assistant cp .env.example .env && sudo -u assistant nano .env && chmod 600 .env
```
Минимум: `BOT_TOKEN`, `GEMINI_API_KEY`, `ADMIN_IDS`,
`DATABASE_URL=postgresql:///assistant?host=/var/run/postgresql` (вход по unix-сокету без пароля),
`REDIS_URL=redis://localhost:6379/1`. Для вебхука — `WEBHOOK_URL` и `WEBHOOK_SECRET` (`openssl rand -hex 32`).
Комментарии в `.env` — только отдельной строкой: systemd не понимает `# ` после значения.

## Служба
```sh
cp deploy/assistant.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now assistant
journalctl -u assistant -f        # «Ассистент запущен»
```

## HTTPS для вебхука (Caddy)
Вебхуки Telegram принимают порты 443, 80, 88, 8443. Пример для Caddy с бесплатным именем sslip.io:
```
ai.130-17-30-251.sslip.io:8443 {
	reverse_proxy 127.0.0.1:8090
}
```
Без вебхука бот работает и через polling (`POLLING=true`, `WEBHOOK_URL` пустой) — HTTPS тогда не нужен.

## Обновление
```sh
sh /home/assistant/ai-assistant-bot/deploy/update.sh    # от root: pull, зависимости, миграции, перезапуск
```

## Бэкап (crontab пользователя assistant)
```
0 4 * * * pg_dump assistant > /home/assistant/backup-$(date +\%a).sql
```

# Развёртывание на сервере (Ubuntu 22.04+)

## 1. Системные зависимости

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git curl
```

## 2. Установка opencode CLI

```bash
curl -fsSL https://opencode.ai/install.sh | bash
export PATH="$HOME/.opencode/bin:$PATH"
opencode --version  # должен показать версию
```

## 3. Клонирование репозитория

```bash
sudo mkdir -p /opt
sudo chown $USER:$USER /opt
git clone <your-repo-url> /opt/my_agent
cd /opt/my_agent
```

## 4. Виртуальное окружение и зависимости

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Настройка .env

```bash
cp .env.example .env
nano .env
```

Заполнить:
- `BOT_TOKEN` — токен Telegram-бота от [@BotFather](https://t.me/BotFather)
- `ROUTERAI_API_KEY` — API-ключ routerai.ru
- `ALLOWED_TELEGRAM_ID` — твой Telegram ID (бот отвечает только одному пользователю)
- `OPENCODE_BIN` — путь к opencode (`~/.opencode/bin/opencode`)

## 6. Тестовый запуск

```bash
source venv/bin/activate
OPENCODE_BIN=~/.opencode/bin/opencode python -m src.bot.main
```

Убедись, что бот отвечает в Telegram, затем останови (Ctrl+C).

## 7. Systemd service

Отредактировать `suertech_agent.service`:

```ini
User=ai_agent            # пользователь, от которого работает бот
WorkingDirectory=/opt/my_agent
ExecStart=/opt/my_agent/venv/bin/python -m src.bot.main
```

Создать пользователя (если ещё нет):

```bash
sudo useradd -r -s /bin/false ai_agent
sudo chown -R ai_agent:ai_agent /opt/my_agent
```

Установить и запустить сервис:

```bash
sudo cp suertech_agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable suertech_agent
sudo systemctl start suertech_agent
sudo systemctl status suertech_agent
```

## 8. Логи

```bash
# Логи бота (systemd)
journalctl -u suertech_agent -f

# JSONL-логи выполнения агента (в корне проекта)
tail -f /opt/my_agent/logs/*.jsonl
```

## 9. Обновление

```bash
cd /opt/my_agent
sudo systemctl stop suertech_agent
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl start suertech_agent
```

## Важно

- Бот рассчитан на одного пользователя (`ALLOWED_TELEGRAM_ID`).
- Без `ROUTERAI_API_KEY` — бот использует только opencode CLI (бесплатный DeepSeek Flash), **tool-calling работать не будет** — Q&A с файловой системой и CODE_TASK-ветка недоступны.
- `logs/` — автогенерируется, добавлена в `.gitignore`.

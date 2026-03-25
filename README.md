# Telegram Broadcast Bot

RU | [EN](#english)

Бесплатный Telegram-бот для автоматической рассылки сообщений по выбранным папкам и чатам через пользовательскую Telegram-сессию.

## Возможности

- Авторизация пользователя через `Telethon`
- Выбор Telegram-папок для рассылки
- Выбор отдельных чатов
- Рассылка текста или медиа с подписью
- Поддержка `custom emoji entities`
- Настройка минимальной и максимальной задержки
- Хранение сессий и настроек в PostgreSQL
- Скрипты установки и обновления для Linux, macOS и Windows

## Стек

- `Python 3`
- `aiogram`
- `Telethon`
- `SQLAlchemy`
- `PostgreSQL` + `asyncpg`
- `python-dotenv`

## Быстрый старт

### 1. Клонирование проекта

```bash
git clone https://github.com/artemspt/auto-telegram-newsletter.git
cd auto-telegram-newsletter
```

### 2. Создание `.env`

Перед запуском любых install/update скриптов сначала создайте и заполните `.env` вручную:

```bash
cp .env.exemple .env
```

После заполнения `.env` уже можно запускать скрипты установки и обновления.

Шаблон содержит такие поля:

```env
BOT_TOKEN=your_bot_token
API_ID=your_api_id
API_HASH=your_api_hash

# Вариант A
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/telegram_bot

# Вариант B
DB_USER=user
DB_PASSWORD=password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=telegram_bot

# Необязательно: первый администратор
ADMIN_ID=123456789
```

Где взять данные:

- `BOT_TOKEN`: у `@BotFather`
- `API_ID` и `API_HASH`: на `https://my.telegram.org/apps`

### 3. Подготовка базы данных

Создайте базу PostgreSQL:

```bash
createdb telegram_bot
```

## Ручной запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

## Как пользоваться

1. Откройте бота и отправьте `/start`
2. Нажмите `рассыл`
3. Пройдите авторизацию по номеру телефона
4. Выберите папки и/или чаты
5. Настройте `текст/медиа`
6. Установите `мин. задержка` и `макс. задержка`
7. Запустите рассылку

## Скрипты установки и обновления

### Linux (`systemd`)

Установка сервиса:

```bash
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

Что делает скрипт:

- создает `.venv`, если его нет
- обновляет `pip`
- устанавливает зависимости
- генерирует unit-файл `systemd`
- включает и запускает сервис `telegrambot`

Обновление:

```bash
chmod +x scripts/update.sh
./scripts/update.sh
```

Что делает:

- выполняет `git pull --ff-only`
- доустанавливает зависимости
- перезапускает сервис `telegrambot`

### macOS (`launchd`)

Установка автозапуска:

```bash
chmod +x scripts/install_service_macos.sh
./scripts/install_service_macos.sh
```

Что делает скрипт:

- создает `.venv`, если его нет
- обновляет `pip`
- устанавливает зависимости
- создает `LaunchAgent` в `~/Library/LaunchAgents`
- запускает бота через `launchd`
- пишет логи в папку `logs/`

Обновление:

```bash
chmod +x scripts/update_macos.sh
./scripts/update_macos.sh
```

Что делает:

- выполняет `git pull --ff-only`
- доустанавливает зависимости
- перезапускает `LaunchAgent`

### Windows

Установка:

```bat
scripts\install_service.bat
```

Обновление:

```bat
scripts\update.bat
```

## Файлы деплоя

- `deploy/telegrambot.service` — шаблон `systemd`-сервиса для Linux
- `deploy/telegrambot.plist` — шаблон `launchd`-агента для macOS
- `scripts/install_service.sh` — установка для Linux
- `scripts/update.sh` — обновление для Linux
- `scripts/install_service_macos.sh` — установка для macOS
- `scripts/update_macos.sh` — обновление для macOS
- `scripts/install_service.bat` — установка для Windows
- `scripts/update.bat` — обновление для Windows

## Важно

- Перед запуском убедитесь, что PostgreSQL доступен и данные в `.env` заполнены корректно

## Контакты

- Telegram: `@sptmanager`
- Почта: `team.spt@zohomail.com`

---

## English

Free Telegram bot for automated broadcasting to selected folders and chats using a user Telegram session.

## Features

- User authorization via `Telethon`
- Folder-based targeting
- Manual chat selection
- Text or media broadcasting
- `custom emoji entities` support
- Configurable min/max delay
- PostgreSQL storage for sessions and settings
- Install and update scripts for Linux, macOS and Windows

## Stack

- `Python 3`
- `aiogram`
- `Telethon`
- `SQLAlchemy`
- `PostgreSQL` + `asyncpg`
- `python-dotenv`

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/artemspt/auto-telegram-newsletter.git
cd auto-telegram-newsletter
```

### 2. Create `.env`

Before running any install/update scripts, create and fill `.env` manually:

```bash
cp .env.exemple .env
```

After `.env` is filled, you can run install and update scripts.

The template contains these fields:

```env
BOT_TOKEN=your_bot_token
API_ID=your_api_id
API_HASH=your_api_hash

DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/telegram_bot

DB_USER=user
DB_PASSWORD=password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=telegram_bot

ADMIN_ID=123456789
```

### 3. Prepare PostgreSQL

```bash
createdb telegram_bot
```

## Manual Run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

## Deployment Scripts

### Linux

Install:

```bash
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

Update:

```bash
chmod +x scripts/update.sh
./scripts/update.sh
```

### macOS

Install:

```bash
chmod +x scripts/install_service_macos.sh
./scripts/install_service_macos.sh
```

Update:

```bash
chmod +x scripts/update_macos.sh
./scripts/update_macos.sh
```

The macOS scripts use `launchd` and create a LaunchAgent in `~/Library/LaunchAgents`.

### Windows

Install:

```bat
scripts\install_service.bat
```

Update:

```bat
scripts\update.bat
```

## Important

- Make sure PostgreSQL is running and `.env` is valid

## Contact

- telegram - @sptmanager
- mail - team.spt@zohomail.com

# Telegram Broadcast Bot

RU | [EN](#english)

Бот для автоматической рассылки сообщений в Telegram по выбранным папкам и чатам.

## Что умеет

- Авторизация пользователя через Telegram (Telethon session)
- Выбор папок Telegram для рассылки
- Выбор отдельных чатов для рассылки
- Рассылка текста или медиа (с подписью)
- Поддержка custom emoji entities
- Настройка минимальной и максимальной задержки
- Хранение настроек и сессий в PostgreSQL

## Технологический стек

- `Python 3`
- `aiogram` (бот-интерфейс и FSM)
- `Telethon` (клиент Telegram от имени пользователя)
- `SQLAlchemy` (async ORM)
- `PostgreSQL` + `asyncpg`
- `python-dotenv`

## Преимущества проекта

- Гибридный подход: удобное управление через бота + отправка через пользовательскую сессию
- Гибкий таргетинг: можно комбинировать папки и отдельные чаты
- Простая эксплуатация: готовые скрипты для установки и обновления сервиса
- Состояние сохраняется: сессии и настройки не теряются после перезапуска

## Установка

### 1. Клонирование и зависимости

```bash
git clone <https://github.com/artemspt/auto-telegram-newsletter>
cd telegrambot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Настройка `.env`

```bash
cp .env.exemple .env
```

Заполните `.env`:

```env
BOT_TOKEN=your_bot_token
API_ID=your_api_id
API_HASH=your_api_hash

# Вариант A (рекомендуется)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/telegram_bot

# Вариант B (если DATABASE_URL пустой)
DB_USER=user
DB_PASSWORD=password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=telegram_bot
```

Где взять credentials:

- `BOT_TOKEN`: у `@BotFather`
- `API_ID` и `API_HASH`: на `https://my.telegram.org/apps`

### 3. Подготовка БД

Создайте базу в PostgreSQL (если еще не создана):

```bash
createdb telegram_bot
```

### 4. Запуск

```bash
python3 main.py
```

## Как пользоваться

1. Откройте бота и отправьте `/start`
2. Нажмите `рассыл`
3. Пройдите авторизацию по номеру телефона
4. Выберите папки и/или чаты
5. Задайте `текст/медиа`
6. Настройте `мин. задержка` и `макс. задержка`
7. Нажмите `Начать рассылку`

## контактная информация
- телеграм - @sptmanager
- почта - team.spt@zohomail.com

---
## English

Telegram bot for automated broadcasts to selected Telegram folders and chats.

## Features

- User authorization via Telegram (Telethon session)
- Folder-based broadcast targeting
- Manual chat selection
- Text or media broadcasting (with caption)
- Custom emoji entities support
- Min/max delay configuration
- PostgreSQL persistence for sessions and settings

## Tech Stack

- `Python 3`
- `aiogram` (bot UI and FSM)
- `Telethon` (user Telegram client)
- `SQLAlchemy` (async ORM)
- `PostgreSQL` + `asyncpg`
- `python-dotenv`

## Project Advantages

- Hybrid model: convenient bot control + sending through user session
- Flexible targeting: combine folders and specific chats
- Easy operations: ready-to-use install/update service scripts
- Persistent state: sessions and settings survive restarts

## Installation

### 1. Clone and install dependencies

```bash
git clone <https://github.com/artemspt/auto-telegram-newsletter>
cd telegrambot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.exemple .env
```

Fill in `.env`:

```env
BOT_TOKEN=your_bot_token
API_ID=your_api_id
API_HASH=your_api_hash

# Option A (recommended)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/telegram_bot

# Option B (if DATABASE_URL is empty)
DB_USER=user
DB_PASSWORD=password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=telegram_bot
```

Credentials:

- `BOT_TOKEN`: from `@BotFather`
- `API_ID` and `API_HASH`: from `https://my.telegram.org/apps`

### 3. Prepare database

Create PostgreSQL database (if not created yet):

```bash
createdb telegram_bot
```

### 4. Run

```bash
python3 main.py
```

## How to Use

1. Open the bot and send `/start`
2. Press `рассыл`
3. Authorize with your phone number
4. Select folders and/or chats
5. Configure `text/media`
6. Set `min delay` and `max delay`
7. Press `Start broadcast`

## systemd Service Deployment

Install and enable autostart:

```bash
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

Update and restart:

```bash
chmod +x scripts/update.sh
./scripts/update.sh
```

## Important

- Never commit real secrets or `.env`
- Ensure your broadcast activity complies with Telegram rules and local laws


## contact information
- telegram - @sptmanager
- mail - team.spt@zohomail.com
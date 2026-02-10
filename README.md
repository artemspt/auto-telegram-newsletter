# Telegram Broadcast Bot

Бот для автоматической рассылки сообщений по папкам и чатам Telegram.

## Установка

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Настройте PostgreSQL базу данных:
```bash
# Создайте базу данных
createdb telegram_bot
```

3. Создайте файл `.env` на основе `.env.example`:
```bash
cp .env.example .env
```

4. Заполните `.env` файл:
```
BOT_TOKEN=your_bot_token_here
DB_USER=telegram_bot_user
DB_PASSWORD=202422
DB_HOST=localhost
DB_PORT=5432
DB_NAME=telegram_bot
```

5. Запустите бота:
```bash
python main.py
```

## Функционал

- ✅ Регистрация сессий Telegram в PostgreSQL
- ✅ Выбор папок из Telegram для рассылки
- ✅ Выбор отдельных чатов для рассылки
- ✅ Настройка количества сообщений на чат
- ✅ Настройка задержек между сообщениями
- ✅ Поддержка текста и медиа для рассылки

## Использование

1. Запустите бота командой `/start`
2. Нажмите "рассыл" для начала работы
3. Авторизуйтесь, отправив номер телефона
4. Выберите папки или чаты для рассылки
5. Настройте текст/медиа и параметры рассылки
6. Запустите рассылку

## Автозапуск и обновление на сервере (systemd)

1. Клонируйте репозиторий и создайте `.env`.
2. Установите сервис и включите автозапуск:
```bash
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

3. Обновление и рестарт одной командой:
```bash
chmod +x scripts/update.sh
./scripts/update.sh
```

## Автозапуск и обновление на сервере (systemd)

1. Клонируйте репозиторий и создайте `.env`.
2. Установите сервис и включите автозапуск:
```bash
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

3. Обновление и рестарт одной командой:
```bash
chmod +x scripts/update.sh
./scripts/update.sh
```
## Автозапуск и обновление на сервере (systemd)

1. Клонируйте репозиторий и создайте `.env`.
2. Установите сервис и включите автозапуск:
```bash
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

3. Обновление и рестарт одной командой:
```bash
chmod +x scripts/update.sh
./scripts/update.sh
```

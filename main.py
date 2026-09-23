import asyncio
import logging
import sys
import random
import os
import time
import tempfile
from datetime import datetime, timezone
from os import getenv

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logging.getLogger("telethon").setLevel(logging.WARNING)

from telethon import TelegramClient, utils
from telethon.sessions import StringSession
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    PeerFloodError,
    RPCError,
    SessionRevokedError,
    UserBannedInChannelError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.tl.types import (
    DialogFilter,
    DialogFilterChatlist,
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityCustomEmoji,
    MessageEntityItalic,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
)
from telethon.tl.functions.messages import GetDialogFiltersRequest


from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.types import KeyboardButton
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram import F

from database import db, User, DEFAULT_MIN_DELAY, DEFAULT_MAX_DELAY
from auth import register_auth_handlers

# Конфигурация
TOKEN = getenv("BOT_TOKEN")
if not TOKEN:
    logging.error("❌ Токен бота не найден! Проверьте файл .env или переменную окружения BOT_TOKEN")
    logging.error("Создайте файл .env в корне проекта и добавьте: BOT_TOKEN=ваш_токен_бота")
    sys.exit(1)

# Проверяем формат токена (должен быть примерно 46 символов и содержать двоеточие)
if len(TOKEN) < 40 or ':' not in TOKEN:
    logging.warning(f"⚠️ Токен выглядит неверно (длина: {len(TOKEN)}). Проверьте правильность токена.")
    logging.warning("Токен должен быть в формате: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")

# API_ID и API_HASH можно получить на https://my.telegram.org/apps
# Они должны быть одинаковыми для всех пользователей вашего приложения
API_ID = getenv("API_ID", "")
API_HASH = getenv("API_HASH", "")
if not API_ID.isdecimal() or not API_HASH:
    logging.error("❌ API_ID и API_HASH не заданы или заданы неверно. Проверьте файл .env")
    sys.exit(1)
API_ID = int(API_ID)

dp = Dispatcher()
# Только личка: в группе номер телефона и код авторизации увидели бы все участники
dp.message.filter(F.chat.type == ChatType.PRIVATE)

# user_id -> (id сессии в БД, клиент)
user_clients: dict[int, tuple[int, TelegramClient]] = {}
active_broadcast_tasks: dict[int, asyncio.Task] = {}
active_broadcast_cancel_events: dict[int, asyncio.Event] = {}
active_broadcast_menu_refs: dict[int, tuple[int, int]] = {}
BOT: Bot = None

MIN_DELAY_SECONDS = 10
MAX_DELAY_SECONDS = 36000 * 60
PER_MESSAGE_DELAY_SECONDS = 3
MAX_CHATS_IN_MENU = 75
MAX_BOT_FILE_SIZE = 20 * 1024 * 1024  # Bot API не отдаёт файлы больше 20 МБ

# Ошибки, после которых сессия пользователя больше не работает
SESSION_DEAD_ERRORS = (
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)


class UserState(StatesGroup):
    waiting_delay = State()
    waiting_text = State()
    wait_phone = State()
    wait_code = State()
    wait_password = State()


def _get_media_info(message: Message):
    """Возвращает (file_id, media_type, file_size) для медиа‑объектов."""
    if message.photo:
        photo = message.photo[-1]
        return photo.file_id, "photo", photo.file_size
    if message.animation:
        return message.animation.file_id, "animation", message.animation.file_size
    if message.video:
        return message.video.file_id, "video", message.video.file_size
    if message.document and message.document.mime_type in ("image/gif", "video/mp4"):
        doc = message.document
        return doc.file_id, doc.mime_type.split("/")[-1], doc.file_size
    return None, None, None


def _extract_message_entities(entities) -> list[dict]:
    if not entities:
        return []
    result = []
    for ent in entities:
        ent_type = getattr(ent, "type", None)
        if not ent_type:
            continue

        item = {
            "type": ent_type,
            "offset": ent.offset,
            "length": ent.length,
        }

        if getattr(ent, "url", None):
            item["url"] = ent.url
        if getattr(ent, "language", None):
            item["language"] = ent.language
        if getattr(ent, "custom_emoji_id", None):
            item["custom_emoji_id"] = ent.custom_emoji_id

        result.append(item)
    return result


def _build_telethon_entities(entities_data):
    if not entities_data:
        return None

    entity_types = {
        "bold": MessageEntityBold,
        "italic": MessageEntityItalic,
        "underline": MessageEntityUnderline,
        "strikethrough": MessageEntityStrike,
        "spoiler": MessageEntitySpoiler,
        "code": MessageEntityCode,
        "blockquote": MessageEntityBlockquote,
    }

    result = []
    for ent in entities_data:
        ent_type = ent.get("type")
        offset = int(ent.get("offset", 0))
        length = int(ent.get("length", 0))

        if ent_type in entity_types:
            result.append(entity_types[ent_type](offset=offset, length=length))
            continue

        if ent_type == "expandable_blockquote":
            result.append(MessageEntityBlockquote(offset=offset, length=length, collapsed=True))
            continue

        if ent_type == "pre":
            result.append(
                MessageEntityPre(
                    offset=offset,
                    length=length,
                    language=ent.get("language", "") or "",
                )
            )
            continue

        if ent_type == "text_link" and ent.get("url"):
            result.append(
                MessageEntityTextUrl(
                    offset=offset,
                    length=length,
                    url=ent["url"],
                )
            )
            continue

        if ent_type == "custom_emoji" and ent.get("custom_emoji_id"):
            result.append(
                MessageEntityCustomEmoji(
                    offset=offset,
                    length=length,
                    document_id=int(ent["custom_emoji_id"]),
                )
            )

    return result or None


async def get_user_client(user_id: int) -> TelegramClient | None:
    """Получить или создать клиент для пользователя"""
    session_obj = await db.get_active_session(user_id)

    cached = user_clients.get(user_id)
    if cached and session_obj and cached[0] == session_obj.id:
        client = cached[1]
        if not client.is_connected():
            await client.connect()
        return client
    if cached:
        # Сессия сменилась (перелогин) или отозвана — старый клиент больше не нужен
        del user_clients[user_id]
        await cached[1].disconnect()

    if not session_obj:
        return None

    client = TelegramClient(StringSession(session_obj.session_string), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        # Сессия невалидна, деактивируем в БД
        await client.disconnect()
        await db.deactivate_sessions(user_id)
        return None

    user_clients[user_id] = (session_obj.id, client)
    return client


async def _drop_user_session(user_id: int):
    await db.deactivate_sessions(user_id)
    cached = user_clients.pop(user_id, None)
    if cached:
        await cached[1].disconnect()


async def _download_bot_file(bot: Bot, file_id: str) -> str:
    file = await bot.get_file(file_id)
    _, ext = os.path.splitext(file.file_path or "")
    suffix = ext if ext else ".bin"
    fd, path = tempfile.mkstemp(prefix="tg_media_", suffix=suffix)
    os.close(fd)
    await bot.download_file(file.file_path, destination=path)
    return path


async def _get_folders(client: TelegramClient) -> list:
    """Пользовательские папки Telegram (без дефолтной «Все чаты»)"""
    result = await client(GetDialogFiltersRequest())
    filters = getattr(result, "filters", result)  # старые слои API возвращают просто список
    return [f for f in filters if isinstance(f, (DialogFilter, DialogFilterChatlist))]


def _folder_title(folder) -> str:
    title = getattr(folder.title, "text", folder.title)
    return f"{folder.emoticon} {title}" if folder.emoticon else title


def _peer_ids(peers) -> set[int]:
    ids = set()
    for peer in peers:
        try:
            ids.add(utils.get_peer_id(peer))
        except TypeError:  # InputPeerSelf и подобные
            pass
    return ids


def _matches_folder_flags(dialog, folder: DialogFilter) -> bool:
    """Подходит ли диалог под флаги папки («все группы», «все каналы», «не прочитанные» и т.д.)"""
    if dialog.is_user:
        entity = dialog.entity
        matched = folder.bots if entity.bot else folder.contacts if entity.contact else folder.non_contacts
    elif dialog.is_group:
        matched = folder.groups
    else:
        matched = folder.broadcasts
    if not matched:
        return False
    if folder.exclude_archived and dialog.archived:
        return False
    if folder.exclude_read and not dialog.unread_count and not dialog.dialog.unread_mark:
        return False
    if folder.exclude_muted:
        mute_until = dialog.dialog.notify_settings.mute_until
        if mute_until and mute_until > datetime.now(timezone.utc):
            return False
    return True


def _folder_dialogs(folder, dialogs) -> list:
    """Диалоги из папки: закреплённые и явно добавленные чаты + чаты по флагам, кроме исключённых"""
    included = _peer_ids(folder.pinned_peers + folder.include_peers)
    if isinstance(folder, DialogFilterChatlist):  # у папок-ссылок нет флагов
        return [d for d in dialogs if d.id in included]
    excluded = _peer_ids(folder.exclude_peers)
    return [
        d for d in dialogs
        if d.id in included or (d.id not in excluded and _matches_folder_flags(d, folder))
    ]


async def _collect_targets(client: TelegramClient, settings) -> list:
    dialogs = await client.get_dialogs()
    targets = {}

    selected_chats = set(settings.selected_chats or [])
    for dialog in dialogs:
        # dialog.entity.id — формат старых сохранений (до перехода на peer_id)
        if dialog.id in selected_chats or dialog.entity.id in selected_chats:
            targets[dialog.id] = dialog.entity

    if settings.selected_folders:
        for folder in await _get_folders(client):
            if folder.id in settings.selected_folders:
                for dialog in _folder_dialogs(folder, dialogs):
                    targets[dialog.id] = dialog.entity

    return list(targets.values())


def _selection_markup(items, selected, kind: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора папок/чатов. items: [(id, иконка, название)], kind: folder | chat"""
    builder = InlineKeyboardBuilder()
    for item_id, icon, title in items:
        mark = "✅" if item_id in selected else icon
        builder.row(InlineKeyboardButton(text=f"{mark} {title[:30]}", callback_data=f"{kind}_{item_id}"))
    builder.row(InlineKeyboardButton(text="✅ Готово", callback_data=f"{kind}s_done"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


def _settings_markup(with_back: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="мин. задержка", callback_data="min_delay"))
    builder.add(InlineKeyboardButton(text="макс. задержка", callback_data="max_delay"))
    builder.row(InlineKeyboardButton(text="текст/медиа", callback_data="text"))
    if with_back:
        builder.row(InlineKeyboardButton(text="назад", callback_data="back_to_broadcast"))
    return builder.as_markup()


@dp.message(CommandStart())
async def command_start_handler(message: Message, state: FSMContext) -> None:
    # /start сбрасывает любой незавершённый ввод
    await state.clear()
    await db.get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )

    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="рассыл"))
    builder.row(KeyboardButton(text="настройки"))
    builder.row(KeyboardButton(text="профиль"))
    builder.row(KeyboardButton(text="поддержка"))

    reply_kb = builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Выбирай"
    )

    await message.answer(
        f"Привет, {html.bold(html.quote(message.from_user.full_name))},\n"
        "Это бот по авто-рассылу! Выберите что хотите сделать.",
        reply_markup=reply_kb
    )


@dp.message(F.text == "рассыл")
async def bot_start_rasil_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    # Проверяем наличие активной сессии
    session_obj = await db.get_active_session(user_id)
    if session_obj:
        # Сессия есть, показываем меню рассылки
        await state.clear()
        await show_broadcast_menu(message, user_id)
    else:
        # Сессии нет, просим авторизоваться
        await message.answer("Для начала работы необходимо авторизоваться.\nПришлите мне ваш номер телефона (в формате +79991234567)")
        await state.set_state(UserState.wait_phone)


async def show_broadcast_menu(message: Message, user_id: int):
    """Показать меню настройки рассылки.

    user_id передаётся явно: у сообщений бота (query.message) from_user — сам бот.
    """
    sent = await message.answer("Выберите действие:", reply_markup=_build_broadcast_menu_markup(user_id))
    active_broadcast_menu_refs[user_id] = (sent.chat.id, sent.message_id)


def _is_broadcast_running(user_id: int) -> bool:
    task = active_broadcast_tasks.get(user_id)
    return task is not None and not task.done()


def _format_seconds(total_seconds: int) -> str:
    days, rest = divmod(max(total_seconds, 0), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if days > 0:
        return f"{days}д {hours}ч {minutes}м"
    if hours > 0:
        return f"{hours}ч {minutes}м"
    if minutes and seconds:
        return f"{minutes}м {seconds}с"
    if minutes:
        return f"{minutes}м"
    return f"{seconds}с"


def _parse_delay(raw: str) -> int | None:
    """«15» или «15м» — минуты, «30с» — секунды. Возвращает секунды или None."""
    raw = raw.strip().lower().replace(" ", "")
    for suffix, unit in (("сек", 1), ("sec", 1), ("с", 1), ("s", 1), ("мин", 60), ("min", 60), ("м", 60), ("m", 60)):
        if raw.endswith(suffix):
            raw = raw[:-len(suffix)]
            break
    else:
        unit = 60
    return int(raw) * unit if raw.isdecimal() else None


def _build_broadcast_menu_markup(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📁 Выбрать папки", callback_data="select_folders"))
    builder.row(InlineKeyboardButton(text="💬 Выбрать чаты", callback_data="select_chats"))
    if _is_broadcast_running(user_id):
        builder.row(InlineKeyboardButton(text="⏹ Закончить рассылку", callback_data="stop_broadcast"))
    else:
        builder.row(InlineKeyboardButton(text="▶️ Начать рассылку", callback_data="start_broadcast"))
    builder.row(InlineKeyboardButton(text="⚙️ Настройки", callback_data="broadcast_settings"))
    return builder.as_markup()


@dp.callback_query(F.data == "select_folders")
async def select_folders_handler(query: CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    client = await get_user_client(user_id)

    if not client:
        await query.answer("Сначала авторизуйтесь!", show_alert=True)
        return

    try:
        folders = [(f.id, "📁", _folder_title(f)) for f in await _get_folders(client)]
    except Exception as e:
        logging.exception(f"Error getting folders for user {user_id}")
        await query.answer(f"Ошибка: {e}"[:200], show_alert=True)
        return

    if not folders:
        await query.answer("У вас нет папок в Telegram. Создайте папки в настройках Telegram", show_alert=True)
        return

    settings = await db.get_broadcast_settings(user_id)
    selected = list(settings.selected_folders or []) if settings else []
    await state.update_data(available_folders=folders, selected_folders=selected)
    await query.message.edit_text(
        f"Выберите папки для рассылки:\n\nНайдено папок: {len(folders)}",
        reply_markup=_selection_markup(folders, selected, "folder")
    )
    await query.answer()


@dp.callback_query(F.data == "select_chats")
async def select_chats_handler(query: CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    client = await get_user_client(user_id)

    if not client:
        await query.answer("Сначала авторизуйтесь!", show_alert=True)
        return

    try:
        dialogs = await client.get_dialogs(limit=MAX_CHATS_IN_MENU)
    except Exception as e:
        logging.exception(f"Error getting chats for user {user_id}")
        await query.answer(f"Ошибка: {e}"[:200], show_alert=True)
        return

    if not dialogs:
        await query.answer("У вас нет чатов", show_alert=True)
        return

    chats = [
        (d.id, "👥" if d.is_group else "📢" if d.is_channel else "💬", d.name or "Unknown")
        for d in dialogs
    ]
    settings = await db.get_broadcast_settings(user_id)
    selected = list(settings.selected_chats or []) if settings else []
    await state.update_data(available_chats=chats, selected_chats=selected)
    await query.message.edit_text(
        f"Выберите чаты для рассылки (показаны последние {MAX_CHATS_IN_MENU}):",
        reply_markup=_selection_markup(chats, selected, "chat")
    )
    await query.answer()


@dp.callback_query(F.data.regexp(r"^(folder|chat)_-?\d+$"))
async def toggle_selection_handler(query: CallbackQuery, state: FSMContext):
    kind, item_id = query.data.split("_", 1)
    item_id = int(item_id)
    data = await state.get_data()

    items = data.get(f"available_{kind}s")
    if items is None:  # данные меню потеряны, например после перезапуска бота
        await query.answer("Меню устарело, откройте выбор заново", show_alert=True)
        return

    selected = data.get(f"selected_{kind}s", [])
    if item_id in selected:
        selected.remove(item_id)
        await query.answer("Убрано из списка")
    else:
        selected.append(item_id)
        await query.answer("Добавлено в список")

    await state.update_data({f"selected_{kind}s": selected})
    await query.message.edit_reply_markup(reply_markup=_selection_markup(items, selected, kind))


@dp.callback_query(F.data.in_({"folders_done", "chats_done"}))
async def selection_done_handler(query: CallbackQuery, state: FSMContext):
    kind = query.data.removesuffix("s_done")
    data = await state.get_data()

    if f"available_{kind}s" not in data:
        await query.answer("Меню устарело, откройте выбор заново", show_alert=True)
        return

    selected = data.get(f"selected_{kind}s", [])
    await db.create_or_update_broadcast_settings(query.from_user.id, **{f"selected_{kind}s": selected})
    await query.answer(f"Выбрано: {len(selected)}" if selected else "Выбор очищен")

    await state.clear()
    await show_broadcast_menu(query.message, query.from_user.id)


@dp.callback_query(F.data == "broadcast_settings")
async def broadcast_settings_handler(query: CallbackQuery):
    await query.message.edit_text("Выберите что хотите настроить:", reply_markup=_settings_markup(with_back=True))
    await query.answer()


@dp.callback_query(F.data == "back_to_broadcast")
async def back_to_broadcast_handler(query: CallbackQuery):
    await show_broadcast_menu(query.message, query.from_user.id)
    await query.answer()


def _start_broadcast(user_id: int):
    cancel_event = asyncio.Event()
    active_broadcast_cancel_events[user_id] = cancel_event
    active_broadcast_tasks[user_id] = asyncio.create_task(_run_broadcast(user_id, cancel_event))


@dp.callback_query(F.data == "start_broadcast")
async def start_broadcast_handler(query: CallbackQuery):
    user_id = query.from_user.id
    active_broadcast_menu_refs[user_id] = (query.message.chat.id, query.message.message_id)
    if _is_broadcast_running(user_id):
        await query.answer("Рассылка уже запущена", show_alert=True)
        return

    client = await get_user_client(user_id)

    if not client:
        await query.answer("Сначала авторизуйтесь!", show_alert=True)
        return

    settings = await db.get_broadcast_settings(user_id)
    if not settings or (not settings.text and not settings.file_id):
        await query.answer("Сначала настройте текст/медиа для рассылки!", show_alert=True)
        return
    if not settings.selected_chats and not settings.selected_folders:
        await query.answer("Сначала выберите папки или чаты для рассылки!", show_alert=True)
        return

    await db.create_or_update_broadcast_settings(user_id, is_running=True)
    _start_broadcast(user_id)

    await query.answer("Рассылка запущена")
    await query.message.edit_reply_markup(reply_markup=_build_broadcast_menu_markup(user_id))


@dp.callback_query(F.data == "stop_broadcast")
async def stop_broadcast_handler(query: CallbackQuery):
    user_id = query.from_user.id
    active_broadcast_menu_refs[user_id] = (query.message.chat.id, query.message.message_id)

    cancel_event = active_broadcast_cancel_events.get(user_id)
    if not _is_broadcast_running(user_id) or cancel_event is None:
        await query.answer("Рассылка не запущена", show_alert=True)
        await query.message.edit_reply_markup(reply_markup=_build_broadcast_menu_markup(user_id))
        return

    cancel_event.set()
    await query.answer("Останавливаю рассылку...")
    try:
        await query.message.edit_reply_markup(reply_markup=_build_broadcast_menu_markup(user_id))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _sleep_or_cancel(cancel_event: asyncio.Event, seconds: float) -> bool:
    """Ждёт seconds секунд. Возвращает True, если рассылку остановили раньше."""
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    return cancel_event.is_set()


async def _send(client: TelegramClient, chat, settings, media_path: str | None):
    if media_path:
        await client.send_file(
            chat,
            media_path,
            caption=settings.caption,
            formatting_entities=_build_telethon_entities(settings.caption_entities),
        )
    else:
        await client.send_message(
            chat,
            settings.text,
            formatting_entities=_build_telethon_entities(settings.text_entities),
        )


# Столько неудачных отправок подряд в разные чаты — значит, проблема в аккаунте, а не в чатах
FAIL_STREAK_LIMIT = 5


def _is_account_error(error: Exception) -> bool:
    """Ошибка аккаунта (спам-ограничение или непонятный сбой), а не конкретного чата"""
    # UserBannedInChannel — не бан в одном чате, а спам-ограничение аккаунта на запись в группы
    return isinstance(error, (PeerFloodError, UserBannedInChannelError)) or not isinstance(error, RPCError)


async def _broadcast_loop(user_id: int, cancel_event: asyncio.Event, progress: dict):
    """Рассылает по кругу, пока не остановят. Ошибки, из-за которых продолжать нельзя, пробрасывает."""
    client = await get_user_client(user_id)
    if not client:
        raise RuntimeError("нет активной сессии Telegram, авторизуйтесь заново")

    settings = await db.get_broadcast_settings(user_id)
    if not settings or (not settings.text and not settings.file_id):
        raise RuntimeError("не настроен текст/медиа")

    chats = await _collect_targets(client, settings)
    if not chats:
        raise RuntimeError("не найдено чатов для рассылки")

    media_path = await _download_bot_file(BOT, settings.file_id) if settings.file_id else None
    fail_streak = 0
    try:
        while not cancel_event.is_set():
            for chat in chats:
                for attempt in range(2):  # при FloodWait ждём и пробуем ещё раз
                    try:
                        await _send(client, chat, settings, media_path)
                        progress["sent"] += 1
                        fail_streak = 0
                        break
                    except FloodWaitError as e:
                        logging.warning(f"FloodWaitError for {chat.id}: wait {e.seconds} seconds")
                        if attempt or await _sleep_or_cancel(cancel_event, e.seconds):
                            break
                    except SESSION_DEAD_ERRORS:
                        raise
                    except Exception as e:
                        logging.error(f"Error sending to {chat.id}: {e}")
                        if _is_account_error(e):
                            fail_streak += 1
                            # Все чаты подряд с ошибкой — тоже аккаунт, даже если чатов меньше лимита
                            if fail_streak >= min(FAIL_STREAK_LIMIT, len(chats)):
                                raise RuntimeError(
                                    f"не удалось отправить в {fail_streak} чатов подряд — вероятно, "
                                    "Telegram ограничил аккаунт. Проверьте его в @SpamBot"
                                )
                        break

                if await _sleep_or_cancel(cancel_event, PER_MESSAGE_DELAY_SECONDS):
                    return

            # Задержка между полными циклами рассылки
            # Нижняя граница страхует от старых настроек с нулевой задержкой
            min_delay = max(settings.min_delay_seconds or 0, MIN_DELAY_SECONDS)
            delay = random.randint(min_delay, max(settings.max_delay_seconds or 0, min_delay))
            await _sleep_or_cancel(cancel_event, delay)
    finally:
        if media_path:
            try:
                os.remove(media_path)
            except OSError:
                pass


async def _notify(user_id: int, text: str):
    try:
        await BOT.send_message(user_id, text)
    except Exception as e:
        logging.warning(f"Failed to notify user {user_id}: {e}")


async def _refresh_broadcast_menu(user_id: int):
    menu_ref = active_broadcast_menu_refs.get(user_id)
    if not menu_ref:
        return
    try:
        await BOT.edit_message_reply_markup(
            chat_id=menu_ref[0],
            message_id=menu_ref[1],
            reply_markup=_build_broadcast_menu_markup(user_id),
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logging.warning(f"Failed to update broadcast menu markup: {e}")


async def _run_broadcast(user_id: int, cancel_event: asyncio.Event):
    started_at = time.time()
    progress = {"sent": 0}
    try:
        await _broadcast_loop(user_id, cancel_event, progress)
        text = "⏹ Рассылка остановлена"
    except asyncio.CancelledError:
        # Бот выключается: is_running остаётся True, после запуска рассылка продолжится
        raise
    except Exception as e:
        if isinstance(e, SESSION_DEAD_ERRORS):
            await _drop_user_session(user_id)
            reason = "сессия Telegram недействительна, авторизуйтесь заново"
        else:
            logging.exception(f"Broadcast failed for user {user_id}")
            reason = str(e)
        text = f"❌ Рассылка остановлена: {html.quote(reason)}"
    finally:
        active_broadcast_tasks.pop(user_id, None)
        active_broadcast_cancel_events.pop(user_id, None)
        elapsed = int(time.time() - started_at)
        await db.increment_broadcast_stats(user_id, sent_inc=progress["sent"], active_seconds_inc=elapsed)

    await db.create_or_update_broadcast_settings(user_id, is_running=False)
    await _notify(user_id, f"{text}\nОтправлено сообщений: {progress['sent']}")
    await _refresh_broadcast_menu(user_id)


@dp.message(F.text == "настройки")
async def settings_menu_handler(message: Message) -> None:
    await message.answer("Выберите что хотите настроить:", reply_markup=_settings_markup(with_back=False))


@dp.message(F.text == "поддержка")
async def bot_support_handler(message: Message) -> None:
    await message.answer("По всем вопросам обращайтесь @spt_support!")


@dp.message(F.text == "профиль")
async def profile_handler(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("Профиль не найден. Нажмите /start.")
        return
    await message.answer(_build_profile_text(user))


def _build_profile_text(user: User) -> str:
    full_name = html.quote(user.full_name or "—")
    created = user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "—"

    total_sent = user.broadcast_sent_total or 0
    total_active = user.broadcast_active_seconds or 0
    return (
        f"<b>👤 Профиль</b>\n"
        f"Имя: {full_name}\n"
        f"Регистрация: <blockquote>{created}</blockquote>\n\n"
        f"<b>📊 Статистика</b>\n"
        f"разослано сообщений: {total_sent}\n"
        f"Время активной рассылки: {_format_seconds(total_active)}\n"
    )


DELAY_LABELS = {"min_delay": "минимальная", "max_delay": "максимальная"}
BAN_RISK_WARNING = (
    f"⚠️ Задержка меньше {_format_seconds(DEFAULT_MIN_DELAY)} повышает риск бана аккаунта Telegram за спам. "
    "Используете на свой риск"
)


@dp.callback_query(F.data.in_(DELAY_LABELS))
async def delay_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    settings = await db.get_broadcast_settings(query.from_user.id)
    defaults = {"min_delay": DEFAULT_MIN_DELAY, "max_delay": DEFAULT_MAX_DELAY}
    current = getattr(settings, f"{query.data}_seconds") if settings else defaults[query.data]

    await state.set_state(UserState.waiting_delay)
    await state.update_data(delay_field=query.data)
    await query.message.reply(
        f"Текущая {DELAY_LABELS[query.data]} задержка: {_format_seconds(current)}\n"
        "Отправь новое значение: число минут (например, 15) или секунд с буквой «с» (например, 30с). "
        f"Минимум {_format_seconds(MIN_DELAY_SECONDS)}\n\n{BAN_RISK_WARNING}"
    )
    await query.answer()


@dp.message(UserState.waiting_delay)
async def process_delay(message: Message, state: FSMContext):
    value = _parse_delay(message.text or "")
    if value is None or not MIN_DELAY_SECONDS <= value <= MAX_DELAY_SECONDS:
        await message.answer(
            f"Введите значение от {_format_seconds(MIN_DELAY_SECONDS)} до {_format_seconds(MAX_DELAY_SECONDS)}, "
            "например 15 (минут) или 30с (секунд)"
        )
        return

    field = (await state.get_data()).get("delay_field", "min_delay")
    settings = await db.get_broadcast_settings(message.from_user.id)
    min_delay = settings.min_delay_seconds if settings else DEFAULT_MIN_DELAY
    max_delay = settings.max_delay_seconds if settings else DEFAULT_MAX_DELAY

    # Вторую границу подтягиваем, чтобы мин. никогда не была больше макс.
    if field == "min_delay":
        min_delay, max_delay = value, max(max_delay, value)
    else:
        min_delay, max_delay = min(min_delay, value), value

    await db.create_or_update_broadcast_settings(
        message.from_user.id,
        min_delay_seconds=min_delay,
        max_delay_seconds=max_delay,
    )
    text = f"✅ Запомнил! Задержка между кругами: от {_format_seconds(min_delay)} до {_format_seconds(max_delay)}"
    if min_delay < DEFAULT_MIN_DELAY:
        text += f"\n\n{BAN_RISK_WARNING}"
    await message.answer(text)
    await state.clear()


@dp.callback_query(F.data == "text")
async def text_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserState.waiting_text)
    settings = await db.get_broadcast_settings(query.from_user.id)
    if settings and (settings.text or settings.file_id):
        if settings.text:
            preview = html.quote(settings.text[:200]) + ("…" if len(settings.text) > 200 else "")
        else:
            preview = "медиа"
        await query.message.reply(
            f"Текущий текст/медиа: {preview}\n"
            "Отправьте новый текст или медиа, которое я буду рассылать"
        )
    else:
        await query.message.reply("Отправьте текст или медиа, которое я буду рассылать")
    await query.answer()


@dp.message(UserState.waiting_text)
async def process_text(message: Message, state: FSMContext):
    file_id, media_type, file_size = _get_media_info(message)

    if file_id is None and not message.text:
        await message.answer("Поддерживаются только текст, фото, видео и GIF. Отправьте что-то из этого")
        return
    if file_size and file_size > MAX_BOT_FILE_SIZE:
        await message.answer("Файл больше 20 МБ — бот не сможет его скачать. Отправьте файл поменьше")
        return

    if file_id is None:
        await db.create_or_update_broadcast_settings(
            message.from_user.id,
            text=message.text,
            file_id=None,
            media_type=None,
            caption=None,
            text_entities=_extract_message_entities(message.entities),
            caption_entities=None,
        )
        await message.answer("✅ Текст сохранен!")
    else:
        await db.create_or_update_broadcast_settings(
            message.from_user.id,
            text=None,
            file_id=file_id,
            media_type=media_type,
            caption=message.caption,
            text_entities=None,
            caption_entities=_extract_message_entities(message.caption_entities),
        )
        await message.answer("✅ Медиа сохранено!")

    await state.clear()


@dp.callback_query(F.data == "cancel")
async def cancel_handler(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌ Отменено")
    await query.answer()


# Регистрируем последними: кнопки главного меню должны работать и во время авторизации
register_auth_handlers(dp, UserState, db, API_ID, API_HASH, show_broadcast_menu)


async def main() -> None:
    global BOT
    await db.init_db()
    logging.info("Database initialized")

    BOT = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Продолжаем рассылки, которые шли до перезапуска
    running = await db.get_running_broadcast_user_ids()
    for user_id in running:
        _start_broadcast(user_id)
    if running:
        logging.info(f"Resumed {len(running)} broadcasts")

    try:
        await dp.start_polling(BOT)
    finally:
        tasks = list(active_broadcast_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())

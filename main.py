import asyncio
import logging
import sys
import random
import os
import time
import tempfile
from datetime import datetime, timezone
from os import getenv
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DialogFilter,
    DialogFilterDefault,
    DialogFilterChatlist,
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    MessageEntityCustomEmoji,
)
from telethon.tl.functions.messages import GetDialogFiltersRequest


from cryptobot_client import CryptoBotService
from cryptobot.models import Status




from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery, MessageEntity
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import F

from database import db, User, UserSession, BroadcastSettings, AppSettings
from auth import register_auth_handlers
from cryptobot_client import CryptoBotService
from cryptobot.models import Asset, Status

# Конфигурация
# API_ID и API_HASH можно получить на https://my.telegram.org/apps
# Они должны быть одинаковыми для всех пользователей вашего приложения
# Сначала пытаемся загрузить из .env, потом из БД
DEFAULT_API_ID = getenv("API_ID")
DEFAULT_API_HASH = getenv("API_HASH")

# Глобальные переменные для API_ID и API_HASH (будут загружены при старте)
API_ID = None
API_HASH = None

async def load_api_credentials():
    """Загрузить API_ID и API_HASH из БД или использовать значения по умолчанию"""
    global API_ID, API_HASH
    
    try:
        # Пытаемся загрузить из БД
        db_api_id = await db.get_app_setting("api_id")
        db_api_hash = await db.get_app_setting("api_hash")
        
        if db_api_id and db_api_hash:
            API_ID = int(db_api_id)
            API_HASH = db_api_hash
            logging.info(f"Loaded API_ID and API_HASH from database")
        else:
            # Используем значения по умолчанию
            API_ID = int(DEFAULT_API_ID) if DEFAULT_API_ID else None
            API_HASH = DEFAULT_API_HASH if DEFAULT_API_HASH else None
            logging.info(f"Using default API_ID and API_HASH from .env")
        
        if not API_ID or not API_HASH:
            logging.warning("API_ID и API_HASH не установлены! Используйте команду /set_api для администратора")
        else:
            logging.info(f"Using API_ID: {API_ID}, API_HASH: {API_HASH[:10]}...")
    except Exception as e:
        logging.error(f"Error loading API credentials: {e}")
        API_ID = int(DEFAULT_API_ID) if DEFAULT_API_ID else None
        API_HASH = DEFAULT_API_HASH if DEFAULT_API_HASH else None


def _get_api_credentials():
    return API_ID, API_HASH

TOKEN = getenv("BOT_TOKEN")
if not TOKEN:
    logging.error("❌ Токен бота не найден! Проверьте файл .env или переменную окружения BOT_TOKEN")
    logging.error("Создайте файл .env в корне проекта и добавьте: BOT_TOKEN=ваш_токен_бота")
    sys.exit(1)

# Проверяем формат токена (должен быть примерно 46 символов и содержать двоеточие)
if len(TOKEN) < 40 or ':' not in TOKEN:
    logging.warning(f"⚠️ Токен выглядит неверно (длина: {len(TOKEN)}). Проверьте правильность токена.")
    logging.warning("Токен должен быть в формате: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
else:
    # Показываем только первые 10 символов для безопасности
    token_preview = TOKEN.split(':')[0] if ':' in TOKEN else TOKEN[:10]
    logging.info(f"Bot token loaded: {token_preview}...")

storage = MemoryStorage()
dp = Dispatcher()

# Временное хранилище для клиентов пользователей
user_clients = {}
active_broadcast_tasks: dict[Any, Any] = {}
active_broadcast_cancel_events = {}
active_broadcast_menu_refs = {}
broadcast_started_once = {}
active_ton_invoices = {}
pending_reviews = {}

BOT = None

MIN_DELAY_MINUTES = 0
MAX_DELAY_MINUTES = 36000

TON_PRICE_MONTH = "0.01"
TON_PRICE_3MONTH = "11"
TON_PRICE_YEAR = "36"

PREMIUM_PLANS = {
    "month": {"label": "1 месяц", "days": 30, "usd": 3, "stars": 1, "ton": TON_PRICE_MONTH},
    "3month": {"label": "3 месяца", "days": 90, "usd": 8, "stars": 2, "ton": TON_PRICE_3MONTH},
    "year": {"label": "12 месяцев", "days": 365, "usd": 32, "stars": 3, "ton": TON_PRICE_YEAR},
}


class UserState(StatesGroup):
    waiting_max_delay = State()
    waiting_min_delay = State()
    waiting_text = State()
    wait_phone = State()
    wait_code = State()
    wait_password = State()
    selecting_folders = State()
    selecting_chats = State()
    waiting_api_id = State()
    waiting_api_hash = State()
    waiting_admin_id = State()
    waiting_refund_data = State()
    waiting_admin_broadcast = State()
    waiting_gift_username = State()
    waiting_gift_plan = State()
    waiting_review = State()
    waiting_gift_friend_username = State()
    waiting_remove_premium_username = State()
    waiting_admin_view_profile_username = State()


def _get_media_info(message: Message):
    """Возвращает (file_id, media_type) для медиа‑объектов."""
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.animation:
        return message.animation.file_id, "animation"
    if message.video:
        return message.video.file_id, "video"
    if message.document and message.document.mime_type in ("image/gif", "video/mp4"):
        return message.document.file_id, message.document.mime_type.split("/")[-1]
    return None, None


def _extract_custom_emoji_entities(entities) -> list[dict]:
    if not entities:
        return []
    result = []
    for ent in entities:
        if getattr(ent, "type", None) == "custom_emoji" and getattr(ent, "custom_emoji_id", None):
            result.append(
                {
                    "type": "custom_emoji",
                    "offset": ent.offset,
                    "length": ent.length,
                    "custom_emoji_id": ent.custom_emoji_id,
                }
            )
    return result


def _build_telethon_entities(entities_data):
    if not entities_data:
        return None
    result = []
    for ent in entities_data:
        if ent.get("type") == "custom_emoji" and ent.get("custom_emoji_id"):
            result.append(
                MessageEntityCustomEmoji(
                    offset=int(ent.get("offset", 0)),
                    length=int(ent.get("length", 0)),
                    document_id=int(ent["custom_emoji_id"]),
                )
            )
    return result or None


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _shift_entities(entities, delta: int):
    if not entities:
        return None
    shifted = []
    for ent in entities:
        shifted.append(
            MessageEntity(
                type=ent.type,
                offset=ent.offset + delta,
                length=ent.length,
                url=ent.url,
                user=ent.user,
                language=ent.language,
                custom_emoji_id=ent.custom_emoji_id,
            )
        )
    return shifted


def _build_review_message(header: str, text: str, entities, bold_header: bool):
    header_len_units = _utf16_len(header)
    if not text:
        result_entities = []
        if bold_header:
            result_entities.append(MessageEntity(type="bold", offset=0, length=header_len_units))
        return header, result_entities or None
    merged = f"{header}\n{text}"
    result_entities = []
    if bold_header:
        result_entities.append(MessageEntity(type="bold", offset=0, length=header_len_units))
    shifted = _shift_entities(entities, header_len_units + _utf16_len("\n"))
    if shifted:
        result_entities.extend(shifted)
    return merged, result_entities or None


async def _notify_premium_purchase(
    bot: Bot,
    buyer_username: str | None,
    buyer_id: int,
    plan_label: str,
    method: str,
    recipient_username: str | None = None,
    recipient_id: int | None = None,
):
    buyer_username = (buyer_username or "").lstrip("@")
    buyer_label = f"@{buyer_username}" if buyer_username else f"user_{buyer_id}"
    recipient_label = None
    if recipient_username or recipient_id:
        normalized = (recipient_username or "").lstrip("@")
        recipient_label = f"@{normalized}" if normalized else f"user_{recipient_id}"

    if recipient_label:
        message = (
            "<b>✅ Покупка премиума</b>\n"
            f"Пользователь: {buyer_label}\n"
            f"Подарил премиум: {recipient_label}\n"
            f"План: {plan_label}\n"
            f"Способ оплаты: {method}"
        )
    else:
        message = (
            "<b>✅ Покупка премиума</b>\n"
            f"Пользователь: {buyer_label}\n"
            f"План: {plan_label}\n"
            f"Способ оплаты: {method}"
        )
    try:
        await bot.send_message(-1003729311086, message)
    except Exception as e:
        logging.warning(f"Failed to notify premium purchase to channel: {e}")


async def get_user_client(user_id: int) -> TelegramClient:
    """Получить или создать клиент для пользователя"""
    if user_id in user_clients:
        return user_clients[user_id]
    
    # Получаем сессию из БД
    session_obj = await db.get_active_session(user_id)
    if not session_obj:
        return None
    
    client = TelegramClient(StringSession(session_obj.session_string), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        # Сессия невалидна, деактивируем в БД
        from sqlalchemy import update
        async with db.async_session() as session:
            await session.execute(
                update(UserSession)
                .where(UserSession.id == session_obj.id)
                .values(is_active=False)
            )
            await session.commit()
        return None
    
    user_clients[user_id] = client
    return client


async def _download_bot_file(bot: Bot, file_id: str) -> str:
    file = await bot.get_file(file_id)
    _, ext = os.path.splitext(file.file_path or "")
    suffix = ext if ext else ".bin"
    fd, path = tempfile.mkstemp(prefix="tg_media_", suffix=suffix)
    os.close(fd)
    await bot.download_file(file.file_path, destination=path)
    return path


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    # Создаем или получаем пользователя
    existing_user = await db.get_user(message.from_user.id)
    payload = None
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1].strip()

    inviter_id = None
    if payload and payload.isdigit():
        inviter_id = int(payload)
        if inviter_id == message.from_user.id:
            inviter_id = None

    if not existing_user:
        await db.create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            invited_by=inviter_id,
        )
        if inviter_id:
            await db.increment_referral(inviter_id, days=1)
            try:
                ref_username = message.from_user.username
                ref_label = f"@{ref_username}" if ref_username else "без username"
                await message.bot.send_message(
                    inviter_id,
                    f"🎉 Новый реферал: {ref_label}. Вам начислен +1 день премиума.",
                )
            except Exception as e:
                logging.warning(f"Failed to notify inviter {inviter_id}: {e}")
    else:
        await db.get_or_create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name
        )
    await db.update_user_info(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="рассыл"))
    builder.row(KeyboardButton(text="настройки"), KeyboardButton(text="получить premium"), KeyboardButton(text="рефералы"))
    builder.row(KeyboardButton(text="профиль"))
    builder.row(KeyboardButton(text="поддержка"))
    
    # Добавляем кнопку для администратора
    is_admin = await db.is_admin(message.from_user.id)
    if is_admin:
        builder.row(KeyboardButton(text="⚙️ Админ панель"))

    reply_kb = builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Выбирай"
    )

    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)},\n"
        "Это бот по авто-рассылу! Выберите что хотите сделать.\n\n"
        "Также доступен резервный бот: @rezerv_god_spt_bot\n"
        "И наш канал с отзывами @reviews_spt",
        reply_markup=reply_kb
    )


@dp.message(F.text == "рассыл")
async def bot_start_rasil_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    
    # Проверяем наличие активной сессии
    session_obj = await db.get_active_session(user_id)
    if session_obj:
        # Сессия есть, показываем меню рассылки
        await show_broadcast_menu(message, state)
    else:
        # Сессии нет, просим авторизоваться
        await message.answer("Для начала работы необходимо авторизоваться.\nПришлите мне ваш номер телефона (в формате +79991234567)")
        await state.set_state(UserState.wait_phone)




async def show_broadcast_menu(message: Message, state: FSMContext):
    """Показать меню настройки рассылки"""
    user_id = message.from_user.id
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📁 Выбрать папки", callback_data="select_folders"))
    builder.row(InlineKeyboardButton(text="💬 Выбрать чаты", callback_data="select_chats"))
    if _is_broadcast_running(user_id):
        builder.row(InlineKeyboardButton(text="⏹ Закончить рассылку", callback_data="stop_broadcast"))
    else:
        builder.row(InlineKeyboardButton(text="▶️ Начать рассылку", callback_data="start_broadcast"))
    builder.row(InlineKeyboardButton(text="⚙️ Настройки", callback_data="broadcast_settings"))

    keyboard = builder.as_markup()
    sent = await message.answer("Выберите действие:", reply_markup=keyboard)
    active_broadcast_menu_refs[user_id] = (sent.chat.id, sent.message_id)


def _build_premium_plans_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PREMIUM_PLANS["month"]["label"], callback_data="premium_plan_month"))
    builder.row(InlineKeyboardButton(text=PREMIUM_PLANS["3month"]["label"], callback_data="premium_plan_3month"))
    builder.row(InlineKeyboardButton(text=PREMIUM_PLANS["year"]["label"], callback_data="premium_plan_year"))
    return builder.as_markup()


def _build_gift_plans_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PREMIUM_PLANS["month"]["label"], callback_data="admin_gift_plan_month"))
    builder.row(InlineKeyboardButton(text=PREMIUM_PLANS["3month"]["label"], callback_data="admin_gift_plan_3month"))
    builder.row(InlineKeyboardButton(text=PREMIUM_PLANS["year"]["label"], callback_data="admin_gift_plan_year"))
    builder.row(InlineKeyboardButton(text="отмена", callback_data="admin_gift_cancel"))
    return builder.as_markup()


async def _send_broadcast_message(bot: Bot, user_id: int, message: Message):
    if message.photo:
        return await bot.send_photo(
            chat_id=user_id,
            photo=message.photo[-1].file_id,
            caption=message.caption or "",
            caption_entities=message.caption_entities,
            parse_mode=None,
        )
    if message.animation:
        return await bot.send_animation(
            chat_id=user_id,
            animation=message.animation.file_id,
            caption=message.caption or "",
            caption_entities=message.caption_entities,
            parse_mode=None,
        )
    if message.video:
        return await bot.send_video(
            chat_id=user_id,
            video=message.video.file_id,
            caption=message.caption or "",
            caption_entities=message.caption_entities,
            parse_mode=None,
        )
    if message.document:
        return await bot.send_document(
            chat_id=user_id,
            document=message.document.file_id,
            caption=message.caption or "",
            caption_entities=message.caption_entities,
            parse_mode=None,
        )
    if message.text:
        return await bot.send_message(
            chat_id=user_id,
            text=message.text,
            entities=message.entities,
            parse_mode=None,
        )
    return await bot.copy_message(
        chat_id=user_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )


async def _show_premium_payment_methods(query: CallbackQuery, plan_key: str):
    plan = PREMIUM_PLANS[plan_key]
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⭐ Stars", callback_data=f"premium_pay_stars_{plan_key}"))
    builder.row(InlineKeyboardButton(text="💎 TON", callback_data=f"premium_pay_ton_{plan_key}"))
    builder.row(InlineKeyboardButton(text="🎁 Подарить премиум другу", callback_data=f"premium_gift_{plan_key}"))
    builder.row(InlineKeyboardButton(text="назад", callback_data="premium_back"))
    await query.message.edit_text(
        f"План: {plan['label']}\n"
        f"Стоимость: ${plan['usd']}\n\n"
        "Выберите способ оплаты:",
        reply_markup=builder.as_markup(),
    )
    await query.answer()

@dp.message(F.text == "отзыв")
async def _send_trial_review_offer(message: Message):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Оставить отзыв", callback_data="leave_review"))
    await message.answer(
        "Одного дня триала вам кажется мало? Нажмите на кнопку оставить отзыв, "
        "напишите его и получите дополнительные 1 день триала!",
        reply_markup=builder.as_markup(),
    )


async def _show_gift_payment_methods(message: Message, plan_key: str, recipient_username: str):
    plan = PREMIUM_PLANS[plan_key]
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⭐ Stars", callback_data=f"premium_gift_pay_stars_{plan_key}"))
    builder.row(InlineKeyboardButton(text="💎 TON", callback_data=f"premium_gift_pay_ton_{plan_key}"))
    builder.row(InlineKeyboardButton(text="назад", callback_data="premium_back"))
    await message.answer(
        f"Подарок для @{recipient_username}\n"
        f"План: {plan['label']}\n"
        f"Стоимость: ${plan['usd']}\n\n"
        "Выберите способ оплаты:",
        reply_markup=builder.as_markup(),
    )


def _is_broadcast_running(user_id: int) -> bool:
    task = active_broadcast_tasks.get(user_id)
    return task is not None and not task.done()


def _format_seconds(total_seconds: int) -> str:
    if total_seconds < 0:
        total_seconds = 0
    minutes = total_seconds // 60
    hours = minutes // 60
    days = hours // 24
    hours = hours % 24
    minutes = minutes % 60
    if days > 0:
        return f"{days}д {hours}ч {minutes}м"
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


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


register_auth_handlers(dp, UserState, db, _get_api_credentials, show_broadcast_menu)


@dp.callback_query(F.data == "select_folders")
async def select_folders_handler(query: CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    client = await get_user_client(user_id)
    
    if not client:
        await query.answer("Сначала авторизуйтесь!", show_alert=True)
        return
    
    try:
        # Проверяем подключение клиента
        if not client.is_connected():
            await client.connect()
        
        # Получаем папки (диалоги с фильтрами) используя правильный метод Telethon
        result = await client(GetDialogFiltersRequest())
        logging.info(f"GetDialogFiltersRequest result type: {type(result)}, attributes: {dir(result)}")
        
        # Проверяем разные возможные атрибуты ответа
        filters = []
        if hasattr(result, 'filters'):
            filters = result.filters
        elif hasattr(result, 'dialog_filters'):
            filters = result.dialog_filters
        elif isinstance(result, list):
            filters = result
        else:
            # Пытаемся получить как список
            try:
                filters = list(result) if result else []
            except:
                filters = []
        
        logging.info(f"Found {len(filters)} filters: {filters}")
        
        if not filters:
            await query.answer("У вас нет папок в Telegram. Создайте папки в настройках Telegram (Desktop/Web версия)", show_alert=True)
            return
        
        builder = InlineKeyboardBuilder()
        folder_list = []
        
        for filter_obj in filters:
            logging.info(f"Filter object: {type(filter_obj)}, {filter_obj}")
            if isinstance(filter_obj, (DialogFilter, DialogFilterChatlist)):
                folder_id = filter_obj.id
                # Получаем название папки
                if hasattr(filter_obj, 'title'):
                    if hasattr(filter_obj.title, 'text'):
                        folder_title = filter_obj.title.text
                    else:
                        folder_title = str(filter_obj.title)
                else:
                    folder_title = f"Папка {folder_id}"
                
                # Добавляем эмодзи если есть
                if isinstance(filter_obj, DialogFilterChatlist) and hasattr(filter_obj, 'emoticon') and filter_obj.emoticon:
                    folder_title = f"{filter_obj.emoticon} {folder_title}"
                
                folder_list.append((folder_id, folder_title))
                builder.row(InlineKeyboardButton(
                    text=f"📁 {folder_title}",
                    callback_data=f"folder_{folder_id}"
                ))
            elif isinstance(filter_obj, DialogFilterDefault):
                # Пропускаем дефолтную папку (все чаты)
                logging.info(f"Skipping DialogFilterDefault (default folder)")
            else:
                logging.warning(f"Unexpected filter type: {type(filter_obj)}, value: {filter_obj}")
        
        if not folder_list:
            await query.answer("Не найдено папок типа DialogFilter. Убедитесь, что у вас есть созданные папки в Telegram.", show_alert=True)
            return
        
        builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="folders_done"))
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
        
        await state.update_data(selecting_folders=True, available_folders=folder_list)
        await query.message.edit_text(
            f"Выберите папки для рассылки:\n\nНайдено папок: {len(folder_list)}",
            reply_markup=builder.as_markup()
        )
        await query.answer()
        
    except Exception as e:
        error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
        await query.answer(f"Ошибка: {error_msg}", show_alert=True)
        logging.error(f"Error getting folders: {e}")


@dp.callback_query(F.data.startswith("folder_"))
async def folder_selected_handler(query: CallbackQuery, state: FSMContext):
    folder_id = int(query.data.split("_")[1])
    data = await state.get_data()
    
    selected_folders = data.get("selected_folders", [])
    if folder_id in selected_folders:
        selected_folders.remove(folder_id)
        await query.answer("Папка убрана из списка")
    else:
        selected_folders.append(folder_id)
        await query.answer("Папка добавлена в список")
    
    await state.update_data(selected_folders=selected_folders)
    
    # Обновляем сообщение с отметками
    builder = InlineKeyboardBuilder()
    folder_list = data.get("available_folders", [])
    
    for fid, title in folder_list:
        mark = "✅" if fid in selected_folders else "📁"
        builder.row(InlineKeyboardButton(
            text=f"{mark} {title}",
            callback_data=f"folder_{fid}"
        ))
    
    builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="folders_done"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    
    await query.message.edit_reply_markup(reply_markup=builder.as_markup())


@dp.callback_query(F.data == "folders_done")
async def folders_done_handler(query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_folders = data.get("selected_folders", [])
    
    if selected_folders:
        # Сохраняем в БД
        await db.create_or_update_broadcast_settings(
            query.from_user.id,
            selected_folders=selected_folders
        )
        await query.answer(f"Выбрано папок: {len(selected_folders)}")
    else:
        await query.answer("Не выбрано ни одной папки", show_alert=True)
    
    await state.clear()
    await show_broadcast_menu(query.message, state)


@dp.callback_query(F.data == "select_chats")
async def select_chats_handler(query: CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    client = await get_user_client(user_id)
    
    if not client:
        await query.answer("Сначала авторизуйтесь!", show_alert=True)
        return
    
    try:
        # Проверяем подключение клиента
        if not client.is_connected():
            await client.connect()
        
        # Получаем все диалоги
        dialogs = await client.get_dialogs(limit=100)
        
        if not dialogs:
            await query.answer("У вас нет чатов", show_alert=True)
            return
        
        builder = InlineKeyboardBuilder()
        chat_list = []
        
        for dialog in dialogs[:75]:  # Ограничиваем 75 чатами
            entity = dialog.entity
            if not entity:
                continue
            
            chat_id = entity.id
            chat_title = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or 'Unknown'
            is_channel = dialog.is_channel
            is_group = dialog.is_group
            
            chat_list.append((chat_id, chat_title, is_channel, is_group))
            
            icon = "📢" if is_channel else "👥" if is_group else "💬"
            builder.row(InlineKeyboardButton(
                text=f"{icon} {chat_title[:30]}",
                callback_data=f"chat_{chat_id}"
            ))
        
        builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="chats_done"))
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
        
        await state.update_data(selecting_chats=True, available_chats=chat_list)
        await query.message.edit_text("Выберите чаты для рассылки (показано до 50):", reply_markup=builder.as_markup())
        await query.answer()
        
    except Exception as e:
        error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
        await query.answer(f"Ошибка: {error_msg}", show_alert=True)
        logging.error(f"Error getting chats: {e}")


@dp.callback_query(F.data.startswith("chat_"))
async def chat_selected_handler(query: CallbackQuery, state: FSMContext):
    chat_id = int(query.data.split("_")[1])
    data = await state.get_data()
    
    selected_chats = data.get("selected_chats", [])
    if chat_id in selected_chats:
        selected_chats.remove(chat_id)
        await query.answer("Чат убран из списка")
    else:
        selected_chats.append(chat_id)
        await query.answer("Чат добавлен в список")
    
    await state.update_data(selected_chats=selected_chats)
    
    # Обновляем сообщение с отметками
    builder = InlineKeyboardBuilder()
    chat_list = data.get("available_chats", [])
    
    for cid, title, is_channel, is_group in chat_list:
        mark = "✅" if cid in selected_chats else ("📢" if is_channel else "👥" if is_group else "💬")
        builder.row(InlineKeyboardButton(
            text=f"{mark} {title[:30]}",
            callback_data=f"chat_{cid}"
        ))
    
    builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="chats_done"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    
    await query.message.edit_reply_markup(reply_markup=builder.as_markup())


@dp.callback_query(F.data == "chats_done")
async def chats_done_handler(query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_chats = data.get("selected_chats", [])
    
    if selected_chats:
        # Сохраняем в БД
        await db.create_or_update_broadcast_settings(
            query.from_user.id,
            selected_chats=selected_chats
        )
        await query.answer(f"Выбрано чатов: {len(selected_chats)}")
    else:
        await query.answer("Не выбрано ни одного чата", show_alert=True)
    
    await state.clear()
    await show_broadcast_menu(query.message, state)




@dp.callback_query(F.data == "broadcast_settings")
async def broadcast_settings_handler(query: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="мин. задержка", callback_data="min_delay"))
    builder.add(InlineKeyboardButton(text="макс. задержка", callback_data="max_delay"))
    builder.row(InlineKeyboardButton(text="текст/медиа", callback_data="text"))
    builder.row(InlineKeyboardButton(text="назад", callback_data="back_to_broadcast"))

    keyboard = builder.as_markup()
    await query.message.edit_text("Выберите что хотите настроить:", reply_markup=keyboard)
    await query.answer()


@dp.callback_query(F.data == "back_to_broadcast")
async def back_to_broadcast_handler(query: CallbackQuery, state: FSMContext):
    await show_broadcast_menu(query.message, state)
    await query.answer()


@dp.callback_query(F.data == "start_broadcast")
async def start_broadcast_handler(query: CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    active_broadcast_menu_refs[user_id] = (query.message.chat.id, query.message.message_id)
    is_first_broadcast = not broadcast_started_once.get(user_id, False)
    if _is_broadcast_running(user_id):
        await query.answer("Рассылка уже запущена", show_alert=True)
        return

    user = await db.get_user(user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not user or (not user.is_premium and (not user.premium_until or user.premium_until < now)):
        await query.answer()
        if user and not user.trial_review_offered:
            await db.set_trial_review_offered(user_id)
            await _send_trial_review_offer(query)
        else:
            await query.message.answer("Доступ к рассылке завершен. Подписка не активна.")
        return

    client = await get_user_client(user_id)
    
    if not client:
        await query.answer("Сначала авторизуйтесь!", show_alert=True)
        return
    
    settings = await db.get_broadcast_settings(user_id)
    if not settings or (not settings.text and not settings.file_id):
        await query.answer("Сначала настройте текст/медиа для рассылки!", show_alert=True)
        return
    
    cancel_event = asyncio.Event()
    active_broadcast_cancel_events[user_id] = cancel_event
    task = asyncio.create_task(_run_broadcast(user_id, cancel_event))
    active_broadcast_tasks[user_id] = task
    broadcast_started_once[user_id] = True

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


async def _run_broadcast(user_id: int, cancel_event: asyncio.Event):
    bot = BOT or Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    menu_ref = active_broadcast_menu_refs.get(user_id)
    started_at = time.time()
    sent_count = 0
    try:
        client = await get_user_client(user_id)
        if not client:
            return

        settings = await db.get_broadcast_settings(user_id)
        if not settings or (not settings.text and not settings.file_id):
            return

        # Получаем список чатов для рассылки (используем set для избежания дубликатов)
        chats_to_send = []
        chat_ids_seen = set()

        if settings.selected_chats:
            dialogs = await client.get_dialogs()
            for dialog in dialogs:
                if dialog.entity and dialog.entity.id in settings.selected_chats and dialog.entity.id not in chat_ids_seen:
                    chats_to_send.append(dialog.entity)
                    chat_ids_seen.add(dialog.entity.id)

        if settings.selected_folders:
            result = await client(GetDialogFiltersRequest())
            # Проверяем разные возможные атрибуты ответа
            filters = []
            if hasattr(result, 'filters'):
                filters = result.filters
            elif hasattr(result, 'dialog_filters'):
                filters = result.dialog_filters
            elif isinstance(result, list):
                filters = result
            for filter_obj in filters:
                if isinstance(filter_obj, (DialogFilter, DialogFilterChatlist)) and filter_obj.id in settings.selected_folders:
                    # Получаем чаты из папки
                    for peer in filter_obj.include_peers:
                        try:
                            if isinstance(peer, (InputPeerChannel, InputPeerChat, InputPeerUser)):
                                entity = await client.get_entity(peer)
                                # Добавляем только если еще не добавлен
                                if entity.id not in chat_ids_seen:
                                    chats_to_send.append(entity)
                                    chat_ids_seen.add(entity.id)
                        except Exception as e:
                            logging.error(f"Error getting entity from folder: {e}")
                            pass

        if not chats_to_send:
            if menu_ref:
                await bot.send_message(menu_ref[0], "❌ Не найдено чатов для рассылки")
            return

        # Отправляем сообщения
        media_path = None
        if settings.file_id:
            try:
                media_path = await _download_bot_file(bot, settings.file_id)
            except Exception as e:
                if menu_ref:
                    await bot.send_message(menu_ref[0], f"❌ Ошибка загрузки медиа: {e}")
                logging.error(f"Media download error: {e}")
                return

        per_message_delay_seconds = 3 if len(chats_to_send) > 1 else 0

        while not cancel_event.is_set():
            for chat in chats_to_send:
                if cancel_event.is_set():
                    break
                try:
                    if settings.file_id:
                        caption_entities = _build_telethon_entities(settings.caption_entities)
                        await client.send_file(
                            chat,
                            media_path,
                            caption=settings.caption,
                            formatting_entities=caption_entities,
                        )
                    else:
                        entities = _build_telethon_entities(settings.text_entities)
                        await client.send_message(chat, settings.text, formatting_entities=entities)

                    sent_count += 1

                    # Задержка между сообщениями
                    if per_message_delay_seconds > 0:
                        try:
                            await asyncio.wait_for(cancel_event.wait(), timeout=per_message_delay_seconds)
                        except asyncio.TimeoutError:
                            pass
                except FloodWaitError as e:
                    logging.warning(f"FloodWaitError for {chat.id}: wait {e.seconds} seconds")
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=e.seconds)
                    except asyncio.TimeoutError:
                        pass
                    if cancel_event.is_set():
                        break
                    try:
                        if settings.file_id:
                            caption_entities = _build_telethon_entities(settings.caption_entities)
                            await client.send_file(
                                chat,
                                media_path,
                                caption=settings.caption,
                                formatting_entities=caption_entities,
                            )
                        else:
                            entities = _build_telethon_entities(settings.text_entities)
                            await client.send_message(chat, settings.text, formatting_entities=entities)
                        sent_count += 1
                    except Exception as inner_e:
                        logging.error(f"Error sending to {chat.id} after FloodWait: {inner_e}")
                except Exception as e:
                    logging.error(f"Error sending to {chat.id}: {e}")

            # Задержка между полными циклами рассылки
            if cancel_event.is_set():
                break
            if settings.min_delay > 0 or settings.max_delay > settings.min_delay:
                delay_minutes = random.randint(settings.min_delay, max(settings.max_delay, settings.min_delay))
                delay_seconds = delay_minutes * 60
                try:
                    await asyncio.wait_for(cancel_event.wait(), timeout=delay_seconds)
                except asyncio.TimeoutError:
                    pass

        if media_path:
            try:
                os.remove(media_path)
            except OSError:
                pass

        if menu_ref:
            if cancel_event.is_set():
                await bot.send_message(menu_ref[0], "⏹ Рассылка остановлена")
            else:
                await bot.send_message(menu_ref[0], f"✅ Рассылка завершена!\nОтправлено сообщений: {sent_count}")
    finally:
        elapsed = int(time.time() - started_at)
        if sent_count or elapsed:
            await db.increment_broadcast_stats(user_id, sent_inc=sent_count, active_seconds_inc=elapsed)
        active_broadcast_tasks.pop(user_id, None)
        active_broadcast_cancel_events.pop(user_id, None)
        if menu_ref:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=menu_ref[0],
                    message_id=menu_ref[1],
                    reply_markup=_build_broadcast_menu_markup(user_id),
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logging.warning(f"Failed to update broadcast menu markup: {e}")
            except Exception as e:
                logging.warning(f"Failed to update broadcast menu markup: {e}")


@dp.message(F.text == "настройки")
async def settings_menu_handler(message: Message) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="мин. задержка", callback_data="min_delay"))
    builder.add(InlineKeyboardButton(text="макс. задержка", callback_data="max_delay"))
    builder.row(InlineKeyboardButton(text="текст", callback_data="text"))
    builder.row(InlineKeyboardButton(text="шаблоны", callback_data="templates"))
    builder.row(InlineKeyboardButton(text="помощь", callback_data="help_setting"))

    keyboard = builder.as_markup()
    await message.answer(f"Выберите что хотите настроить:", reply_markup=keyboard)


@dp.message(F.text == "получить premium")
async def premium_offer_handler(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if user and user.is_premium and user.premium_until and user.premium_until >= now:
        status = await db.get_premium_status(message.from_user.id)
        premium_started_at = status.get("premium_started_at") if status else None
        premium_until = status.get("premium_until") if status else None
        remaining = status.get("remaining") if status else None
        remaining_text = "—"
        if remaining is not None:
            total_minutes = int(remaining.total_seconds() // 60)
            days = total_minutes // (24 * 60)
            hours = (total_minutes % (24 * 60)) // 60
            minutes = total_minutes % 60
            remaining_text = f"{days}д {hours}ч {minutes}м"
        started_text = premium_started_at.strftime("%Y-%m-%d %H:%M") if premium_started_at else "—"
        until_text = premium_until.strftime("%Y-%m-%d %H:%M") if premium_until else "—"
        if user.gift_from_username:
            bought_label = f"Подарен @{user.gift_from_username}"
        elif user.premium_plan == "trial" and not user.last_payment_charge_id:
            bought_label = "Пробный доступ"
            started_text = "—"
        else:
            bought_label = "Куплен"
        keyboard = _build_premium_plans_keyboard()
        await message.answer(
            "<b>✅ У вас уже активирован премиум.</b>\n"
            "<b>Если вы хотите продлить свой план то выберите план повторно, он активируется сразу после окончания этого.</b>\n\n"
            "<blockquote>"
            f"{bought_label}: {started_text}\n"
            f"Закончится: {until_text}\n"
            f"Осталось: {remaining_text}"
            "</blockquote>",
            reply_markup=keyboard,
        )
        return

    keyboard = _build_premium_plans_keyboard()
    await message.answer(
        "У вас еще нет премиума. Предлагаем оформить подписку:\n"
        "Выберите подходящий план:",
        reply_markup=keyboard
    )


@dp.message(F.text == "поддержка")
async def bot_support_handler(message: Message) -> None:
    await message.answer(f"По всем вопросам обращайтесь @spt_support!")


@dp.message(F.text == "рефералы")
async def referrals_handler(message: Message) -> None:
    me = await message.bot.get_me()
    link = f"https://t.me/{me.username}?start={message.from_user.id}"
    user = await db.get_user(message.from_user.id)
    referral_count = user.referral_count if user else 0
    await message.answer(
        "👥 Рефералы\n"
        f"Приглашено: {referral_count}\n\n"
        f"Ваша ссылка:\n{link}"
    )


@dp.message(F.text == "профиль")
async def profile_handler(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("Профиль не найден. Нажмите /start.")
        return
    profile_text = await _build_profile_text(user)
    await message.answer(profile_text)

async def _build_profile_text(user: User) -> str:
    status = await db.get_premium_status(user.telegram_id)
    full_name = user.full_name or "—"
    created = user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "—"

    premium_label = "нет"
    started_text = "—"
    until_text = "—"
    remaining_text = "—"

    if status and status.get("remaining") and status["remaining"].total_seconds() > 0:
        premium_label = "активен"
        started = status.get("premium_started_at")
        until = status.get("premium_until")
        started_text = started.strftime("%Y-%m-%d %H:%M") if started else "—"
        until_text = until.strftime("%Y-%m-%d %H:%M") if until else "—"
        remaining = status.get("remaining")
        if remaining is not None:
            total_minutes = int(remaining.total_seconds() // 60)
            days = total_minutes // (24 * 60)
            hours = (total_minutes % (24 * 60)) // 60
            minutes = total_minutes % 60
            remaining_text = f"{days}д {hours}ч {minutes}м"

    source_label = "Куплен"
    if user.gift_from_username:
        source_label = f"Подарен (@{user.gift_from_username})"
    elif user.premium_plan == "trial" and not user.last_payment_charge_id:
        source_label = "Пробный доступ"
        started_text = "—"

    total_sent = user.broadcast_sent_total or 0
    total_active = user.broadcast_active_seconds or 0
    referral_count = user.referral_count or 0
    return (
        f"<b>👤 Профиль</b>\n"
        f"Имя: {full_name}\n"
        f"Регистрация: <blockquote>{created}</blockquote>\n\n"
        f"<b>💎 Премиум</b>\n"
        f"Статус: {premium_label}\n"
        f"<blockquote>"
        f"{source_label}: {started_text}\n"
        f"Закончится: {until_text}\n"
        f"Осталось: {remaining_text}"
        f"</blockquote>\n\n"
        f"<b>📊 Статистика</b>\n"
        f"разослано сообщений: {total_sent}\n"
        f"Время активной рассылки: {_format_seconds(total_active)}\n"
        f"Приглашено рефералов: {referral_count}\n\n"
    )


@dp.callback_query(F.data == "premium_plan_month")
async def premium_plan_month_handler(query: CallbackQuery) -> None:
    await _show_premium_payment_methods(query, "month")


@dp.callback_query(F.data == "premium_plan_3month")
async def premium_plan_3month_handler(query: CallbackQuery) -> None:
    await _show_premium_payment_methods(query, "3month")


@dp.callback_query(F.data == "premium_plan_year")
async def premium_plan_year_handler(query: CallbackQuery) -> None:
    await _show_premium_payment_methods(query, "year")


@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@dp.callback_query(F.data == "premium_back")
async def premium_back_handler(query: CallbackQuery) -> None:
    await query.message.edit_text(
        "Выберите подходящий план:",
        reply_markup=_build_premium_plans_keyboard(),
    )
    await query.answer()


@dp.callback_query(F.data.startswith("premium_gift_") & ~F.data.startswith("premium_gift_pay_"))
async def premium_gift_handler(query: CallbackQuery, state: FSMContext) -> None:
    plan_key = query.data.replace("premium_gift_", "")
    if plan_key not in PREMIUM_PLANS:
        await query.answer("Неизвестный план", show_alert=True)
        return
    await state.update_data(gift_plan_key=plan_key)
    await query.message.answer("Введите username друга:")
    await state.set_state(UserState.waiting_gift_friend_username)
    await query.answer()


@dp.message(UserState.waiting_gift_friend_username)
async def process_gift_friend_username(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправьте username текстом")
        return
    username = message.text.strip().lstrip("@")
    if not username:
        await message.answer("Username не может быть пустым")
        return
    recipient = await db.get_user_by_username(username)
    if not recipient:
        await message.answer("❌ Пользователь не найден. Он должен хотя бы раз написать боту.")
        await state.clear()
        return
    data = await state.get_data()
    plan_key = data.get("gift_plan_key")
    if not plan_key or plan_key not in PREMIUM_PLANS:
        await message.answer("Не выбран план")
        await state.clear()
        return
    await state.update_data(
        gift_recipient_id=recipient.telegram_id,
        gift_recipient_username=recipient.username or username,
    )
    await _show_gift_payment_methods(message, plan_key, recipient.username or username)
    await state.set_state(None)


@dp.callback_query(F.data == "leave_review")
async def leave_review_handler(query: CallbackQuery, state: FSMContext) -> None:
    user = await db.get_user(query.from_user.id)
    if user and user.trial_review_used:
        await query.answer("Вы уже получили триал за отзыв", show_alert=True)
        return
    await query.message.answer("Напишите отзыв текстом или отправьте фото с подписью:")
    await state.set_state(UserState.waiting_review)
    await query.answer()


@dp.message(UserState.waiting_review)
async def process_review(message: Message, state: FSMContext):
    review_text = None
    review_photo_id = None
    review_entities = None
    if message.photo:
        review_photo_id = message.photo[-1].file_id
        review_text = message.caption or ""
        review_entities = message.caption_entities
    else:
        review_text = message.text or ""
        review_entities = message.entities
    if not review_text and not review_photo_id:
        await message.answer("Пожалуйста, отправьте отзыв текстом или с фотографией")
        return
    user = await db.get_user(message.from_user.id)
    if user and user.trial_review_used:
        await message.answer("Вы уже получили триал за отзыв.")
        await state.clear()
        return
    review_text = review_text.strip()
    logging.info(f"Review received from {message.from_user.id}: {review_text}")
    pending_reviews[message.from_user.id] = {
        "text": review_text,
        "photo_id": review_photo_id,
        "entities": review_entities,
    }

    admin_ids = await db.get_admin_ids()
    if not admin_ids:
        await message.answer("⚠️ Нет администраторов для проверки отзыва.")
        await state.clear()
        return

    review_username = message.from_user.username or f"user_{message.from_user.id}"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"review_accept_{message.from_user.id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"review_reject_{message.from_user.id}"),
    )
    for admin_id in admin_ids:
        try:
            header = f"📝 Новый отзыв от @{review_username}"
            admin_text, admin_entities = _build_review_message(
                header,
                review_text,
                review_entities,
                bold_header=False,
            )
            if review_photo_id:
                await message.bot.send_photo(
                    admin_id,
                    review_photo_id,
                    caption=admin_text,
                    caption_entities=admin_entities,
                    parse_mode=None,
                    reply_markup=builder.as_markup(),
                )
            else:
                await message.bot.send_message(
                    admin_id,
                    admin_text,
                    entities=admin_entities,
                    parse_mode=None,
                    reply_markup=builder.as_markup(),
                )
        except Exception as e:
            logging.warning(f"Failed to send review to admin {admin_id}: {e}")

    await message.answer("✅ Спасибо! Отзыв отправлен администраторам на проверку.")
    await state.clear()


@dp.callback_query(F.data.startswith("review_accept_"))
async def review_accept_handler(query: CallbackQuery) -> None:
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    try:
        target_id = int(query.data.replace("review_accept_", ""))
    except ValueError:
        await query.answer("Неверные данные", show_alert=True)
        return

    target_user = await db.get_user(target_id)
    if not target_user:
        await query.answer("Пользователь не найден", show_alert=True)
        return
    if target_user.trial_review_used:
        await query.answer("Отзыв уже был подтвержден ранее", show_alert=True)
        return

    await db.add_trial_days(target_id, days=3)
    await db.set_trial_review_used(target_id)
    review_payload = pending_reviews.pop(target_id, None)
    if review_payload:
        review_text = review_payload.get("text") or ""
        review_photo_id = review_payload.get("photo_id")
        review_entities = review_payload.get("entities")
        try:
            header = f"Пользователь @{target_user.username or f'user_{target_id}'} оставил отзыв о боте"
            channel_text, channel_entities = _build_review_message(
                header,
                review_text,
                review_entities,
                bold_header=True,
            )
            if review_photo_id:
                await query.message.bot.send_photo(
                    -1003729311086,
                    review_photo_id,
                    caption=channel_text,
                    caption_entities=channel_entities,
                    parse_mode=None,
                )
            else:
                await query.message.bot.send_message(
                    -1003729311086,
                    channel_text,
                    entities=channel_entities,
                    parse_mode=None,
                )
        except Exception as e:
            logging.warning(f"Failed to send accepted review to channel: {e}")
    try:
        await query.message.bot.send_message(
            target_id,
            "✅ Ваш отзыв успешно принят! Вам начислено +3 дня триала.",
        )
    except Exception as e:
        logging.warning(f"Failed to notify review accept to {target_id}: {e}")

    try:
        await query.message.edit_text(query.message.text + "\n\n✅ Отзыв принят")
    except Exception as e:
        logging.warning(f"Failed to update review message: {e}")
    await query.answer()


@dp.callback_query(F.data.startswith("review_reject_"))
async def review_reject_handler(query: CallbackQuery) -> None:
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    try:
        target_id = int(query.data.replace("review_reject_", ""))
    except ValueError:
        await query.answer("Неверные данные", show_alert=True)
        return

    target_user = await db.get_user(target_id)
    if not target_user:
        await query.answer("Пользователь не найден", show_alert=True)
        return

    try:
        await query.message.bot.send_message(
            target_id,
            "❌ Ваш отзыв отклонен.",
        )
    except Exception as e:
        logging.warning(f"Failed to notify review reject to {target_id}: {e}")

    try:
        await query.message.edit_text(query.message.text + "\n\n❌ Отзыв отклонен")
    except Exception as e:
        logging.warning(f"Failed to update review message: {e}")
    pending_reviews.pop(target_id, None)
    await query.answer()


@dp.callback_query(F.data.startswith("premium_pay_stars_"))
async def premium_pay_stars_handler(query: CallbackQuery) -> None:
    plan_key = query.data.replace("premium_pay_stars_", "")
    plan = PREMIUM_PLANS.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return
    await query.answer()
    await query.message.answer_invoice(
        title=f"Премиум на {plan['label']}",
        description=f"Доступ к рассылке на {plan['days']} дней. Цена ${plan['usd']}.",
        payload=f"premium:{plan_key}:{plan['days']}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["label"], amount=plan["stars"])],
    )


@dp.callback_query(F.data.startswith("premium_gift_pay_stars_"))
async def premium_gift_pay_stars_handler(query: CallbackQuery, state: FSMContext) -> None:
    plan_key = query.data.replace("premium_gift_pay_stars_", "")
    plan = PREMIUM_PLANS.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return

    data = await state.get_data()
    recipient_id = data.get("gift_recipient_id")
    recipient_username = data.get("gift_recipient_username")
    if not recipient_id or not recipient_username:
        await query.answer("Сначала укажите получателя", show_alert=True)
        return

    gifter_username = query.from_user.username or f"user_{query.from_user.id}"
    await query.answer()
    await query.message.answer_invoice(
        title=f"Премиум для @{recipient_username}",
        description=f"Подарок на {plan['days']} дней. Цена ${plan['usd']}.",
        payload=f"gift:{plan_key}:{plan['days']}:{recipient_id}:{gifter_username}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["label"], amount=plan["stars"])],
    )
    await state.clear()

'''
@dp.callback_query(F.data.startswith("premium_pay_ton_test_"))
async def premium_pay_ton_test_handler(query: CallbackQuery) -> None:
    plan_key = query.data.replace("premium_pay_ton_test_", "")
    plan = PREMIUM_PLANS.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return

    ton_price = {
        "month": TON_PRICE_MONTH,
        "3month": TON_PRICE_3MONTH,
        "year": TON_PRICE_YEAR,
    }.get(plan_key, "X.XX")

    api_token = getenv("CRYPTOBOT_TESTNET_API_TOKEN")
    try:
        crypto = CryptoBotService(api_token=api_token, is_mainnet=False)
    except Exception as e:
        await query.message.answer(f"❌ CryptoBot testnet не настроен: {e}")
        await query.answer()
        return

    payload = f"premium:{plan_key}:{plan['days']}:{query.from_user.id}:{int(time.time())}"
    try:
        invoice = await crypto.create_invoice(
            asset=Asset.TON,
            amount=float(ton_price),
            description=f"Премиум {plan['label']} (testnet)",
            payload=payload,
            allow_comments=True,
            allow_anonymous=False,
            expires_in=3600,
        )
    except Exception as e:
        logging.exception(
            "CryptoBot create_invoice failed (testnet)",
            extra={
                "user_id": query.from_user.id,
                "plan_key": plan_key,
                "ton_price": ton_price,
            },
        )
        await query.message.answer(f"❌ Ошибка создания счета (testnet): {e}")
        await query.answer()
        return

    active_ton_invoices[query.from_user.id] = {
        "invoice_id": invoice.invoice_id,
        "plan_key": plan_key,
        "network": "testnet",
    }

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Я оплатил TON", callback_data=f"premium_ton_paid_{plan_key}"))
    builder.row(InlineKeyboardButton(text="назад", callback_data="premium_back"))

    await query.message.answer(
        "🧪 Оплата TON (testnet)\n\n"
        f"План: {plan['label']}\n"
        f"Стоимость: {ton_price} TON\n"
        f"Счет: {invoice.bot_invoice_url}\n\n"
        "После оплаты нажмите кнопку ниже для проверки.",
        reply_markup=builder.as_markup(),
    )
    await query.answer()
'''

@dp.callback_query(F.data.startswith("premium_pay_ton_"))
async def premium_pay_ton_handler(query: CallbackQuery) -> None:
    plan_key = query.data.replace("premium_pay_ton_", "")
    plan = PREMIUM_PLANS.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return

    ton_price = {
        "month": TON_PRICE_MONTH,
        "3month": TON_PRICE_3MONTH,
        "year": TON_PRICE_YEAR,
    }.get(plan_key, "X.XX")

    try:
        crypto = CryptoBotService()
    except Exception as e:
        await query.message.answer(f"❌ CryptoBot не настроен: {e}")
        await query.answer()
        return
    payload = f"premium:{plan_key}:{plan['days']}:{query.from_user.id}:{int(time.time())}"
    try:
        invoice = await crypto.create_invoice(
            asset=Asset.TON,
            amount=float(ton_price),
            description=f"Премиум {plan['label']}",
            payload=payload,
            allow_comments=True,
            allow_anonymous=False,
            expires_in=3600,
        )
    except Exception as e:
        logging.exception(
            "CryptoBot create_invoice failed",
            extra={
                "user_id": query.from_user.id,
                "plan_key": plan_key,
                "ton_price": ton_price,
            },
        )
        await query.message.answer(f"❌ Ошибка создания счета: {e}")
        await query.answer()
        return

    active_ton_invoices[query.from_user.id] = {
        "invoice_id": invoice.invoice_id,
        "plan_key": plan_key,
        "network": "mainnet",
    }

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Я оплатил TON", callback_data=f"premium_ton_paid_{plan_key}"))
    builder.row(InlineKeyboardButton(text="назад", callback_data="premium_back"))

    await query.message.answer(
        "💎 Оплата TON\n\n"
        f"План: {plan['label']}\n"
        f"Стоимость: {ton_price} TON\n"
        f"Счет: {invoice.bot_invoice_url}\n\n"
        "После оплаты нажмите кнопку ниже для проверки.",
        reply_markup=builder.as_markup(),
    )
    await query.answer()


@dp.callback_query(F.data.startswith("premium_gift_pay_ton_"))
async def premium_gift_pay_ton_handler(query: CallbackQuery, state: FSMContext) -> None:
    plan_key = query.data.replace("premium_gift_pay_ton_", "")
    plan = PREMIUM_PLANS.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return

    data = await state.get_data()
    recipient_id = data.get("gift_recipient_id")
    recipient_username = data.get("gift_recipient_username")
    if not recipient_id or not recipient_username:
        await query.answer("Сначала укажите получателя", show_alert=True)
        return

    ton_price = {
        "month": TON_PRICE_MONTH,
        "3month": TON_PRICE_3MONTH,
        "year": TON_PRICE_YEAR,
    }.get(plan_key, "X.XX")

    try:
        crypto = CryptoBotService()
    except Exception as e:
        await query.message.answer(f"❌ CryptoBot не настроен: {e}")
        await query.answer()
        return

    gifter_username = query.from_user.username or f"user_{query.from_user.id}"
    payload = f"gift:{plan_key}:{plan['days']}:{recipient_id}:{gifter_username}"
    try:
        invoice = await crypto.create_invoice(
            asset=Asset.TON,
            amount=float(ton_price),
            description=f"Премиум для @{recipient_username}",
            payload=payload,
            allow_comments=True,
            allow_anonymous=False,
            expires_in=3600,
        )
    except Exception as e:
        logging.exception(
            "CryptoBot create_invoice failed (gift)",
            extra={
                "user_id": query.from_user.id,
                "plan_key": plan_key,
                "ton_price": ton_price,
            },
        )
        await query.message.answer(f"❌ Ошибка создания счета: {e}")
        await query.answer()
        return

    active_ton_invoices[query.from_user.id] = {
        "invoice_id": invoice.invoice_id,
        "plan_key": plan_key,
        "network": "mainnet",
        "recipient_id": recipient_id,
        "recipient_username": recipient_username,
        "gift_from_username": gifter_username,
    }

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Я оплатил TON", callback_data=f"premium_ton_paid_{plan_key}"))
    builder.row(InlineKeyboardButton(text="назад", callback_data="premium_back"))

    await query.message.answer(
        "💎 Оплата TON\n\n"
        f"Подарок для @{recipient_username}\n"
        f"План: {plan['label']}\n"
        f"Стоимость: {ton_price} TON\n"
        f"Счет: {invoice.bot_invoice_url}\n\n"
        "После оплаты нажмите кнопку ниже для проверки.",
        reply_markup=builder.as_markup(),
    )
    await state.clear()
    await query.answer()


@dp.callback_query(F.data.startswith("premium_ton_paid_"))
async def premium_ton_paid_handler(query: CallbackQuery) -> None:
    plan_key = query.data.replace("premium_ton_paid_", "")
    plan = PREMIUM_PLANS.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return
    invoice_info = active_ton_invoices.get(query.from_user.id)
    if not invoice_info:
        await query.answer("Нет активного счета", show_alert=True)
        return

    network = invoice_info.get("network", "mainnet")
    try:
        if network == "testnet":
            crypto = CryptoBotService(
                api_token=getenv("CRYPTOBOT_TESTNET_API_TOKEN"),
                is_mainnet=False,
            )
        else:
            crypto = CryptoBotService()
    except Exception as e:
        await query.answer(f"CryptoBot не настроен: {e}", show_alert=True)
        return
    try:
        invoices = await crypto.get_invoices(invoice_ids=str(invoice_info["invoice_id"]))
    except Exception as e:
        logging.exception(
            "CryptoBot get_invoices failed: invoice_id=%s network=%s user_id=%s",
            invoice_info["invoice_id"],
            network,
            query.from_user.id,
        )
        await query.answer(f"Ошибка проверки: {e}", show_alert=True)
        return

    if not invoices:
        logging.warning(
            f"CryptoBot get_invoices returned empty list: invoice_id={invoice_info['invoice_id']} "
            f"network={network} user_id={query.from_user.id}"
        )
        await query.answer("Оплата пока не найдена", show_alert=True)
        return

    invoice = invoices[0]
    status_value = getattr(invoice, "status", None)
    status_str = status_value.value if hasattr(status_value, "value") else str(status_value)
    if status_str != Status.paid.value:
        await query.answer("Оплата пока не найдена", show_alert=True)
        return

    recipient_id = invoice_info.get("recipient_id")
    gift_from_username = invoice_info.get("gift_from_username")
    if recipient_id:
        await db.set_premium(recipient_id, plan_key, plan["days"], gift_from_username=gift_from_username)
        await query.message.answer("✅ Подарок оплачен. Премиум активирован для получателя.")
        await _notify_premium_purchase(
            query.message.bot,
            query.from_user.username,
            query.from_user.id,
            plan["label"],
            "TON",
            recipient_username=invoice_info.get("recipient_username"),
            recipient_id=recipient_id,
        )
        try:
            sender_name = gift_from_username or "пользователь"
            await query.message.bot.send_message(
                recipient_id,
                f"🎁 @{sender_name} подарил вам премиум на {plan['label']}.",
            )
        except Exception as e:
            logging.warning(f"Failed to notify gift recipient {recipient_id}: {e}")
    else:
        await db.set_premium(query.from_user.id, plan_key, plan["days"], gift_from_username=None)
        await query.message.answer("✅ Премиум активирован. Спасибо за оплату!")
        await _notify_premium_purchase(
            query.message.bot,
            query.from_user.username,
            query.from_user.id,
            plan["label"],
            "TON",
        )
    active_ton_invoices.pop(query.from_user.id, None)
    await query.answer()


@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payload = (message.successful_payment.invoice_payload or "").strip()
    if not (payload.startswith("premium:") or payload.startswith("gift:")):
        await message.answer("✅ Оплата прошла успешно.")
        return

    parts = payload.split(":")
    if payload.startswith("premium:") and len(parts) != 3:
        await message.answer("✅ Оплата прошла успешно.")
        return
    if payload.startswith("gift:") and len(parts) < 5:
        await message.answer("✅ Оплата прошла успешно.")
        return

    if payload.startswith("premium:"):
        plan = parts[1]
        try:
            duration_days = int(parts[2])
        except ValueError:
            await message.answer("✅ Оплата прошла успешно.")
            return
        plan_label = PREMIUM_PLANS.get(plan, {}).get("label", plan)
        await db.set_premium(message.from_user.id, plan, duration_days, gift_from_username=None)
        if message.successful_payment.telegram_payment_charge_id:
            await db.set_last_payment_charge_id(
                message.from_user.id,
                message.successful_payment.telegram_payment_charge_id,
            )
        await _notify_premium_purchase(
            message.bot,
            message.from_user.username,
            message.from_user.id,
            plan_label,
            "Stars",
        )
        await message.answer("✅ Премиум активирован. Спасибо за покупку!")
        return

    plan = parts[1]
    try:
        duration_days = int(parts[2])
        recipient_id = int(parts[3])
    except ValueError:
        await message.answer("✅ Оплата прошла успешно.")
        return
    gifter_username = parts[4]
    plan_label = PREMIUM_PLANS.get(plan, {}).get("label", plan)
    await db.set_premium(recipient_id, plan, duration_days, gift_from_username=gifter_username)
    if message.successful_payment.telegram_payment_charge_id:
        await db.set_last_payment_charge_id(
            message.from_user.id,
            message.successful_payment.telegram_payment_charge_id,
        )
    recipient_user = await db.get_user(recipient_id)
    recipient_username = recipient_user.username if recipient_user else None
    await _notify_premium_purchase(
        message.bot,
        message.from_user.username,
        message.from_user.id,
        plan_label,
        "Stars",
        recipient_username=recipient_username,
        recipient_id=recipient_id,
    )
    await message.answer("✅ Подарок оплачен. Премиум активирован для получателя.")
    try:
        plan_label = PREMIUM_PLANS.get(plan, {}).get("label", plan)
        await message.bot.send_message(
            recipient_id,
            f"🎁 @{gifter_username} подарил вам премиум на {plan_label}.",
        )
    except Exception as e:
        logging.warning(f"Failed to notify gift recipient {recipient_id}: {e}")


@dp.callback_query(F.data == "min_delay")
async def min_delay_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserState.waiting_min_delay)
    settings = await db.get_broadcast_settings(query.from_user.id)
    if settings and settings.min_delay is not None:
        await query.message.reply(
            f"Текущая минимальная задержка: {settings.min_delay} мин\n"
            "Отправь новое значение (в минутах!)"
        )
    else:
        await query.message.reply("Отправь мне время минимальной задержки между сообщениями (в минутах!)")
    await query.answer()


@dp.message(UserState.waiting_min_delay)
async def process_min_delay(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer(f"Отправь мне именно число")
    else:
        value = int(message.text)
        if value < MIN_DELAY_MINUTES or value > MAX_DELAY_MINUTES:
            await message.answer(f"Введите число от {MIN_DELAY_MINUTES} до {MAX_DELAY_MINUTES}")
            return
        settings = await db.get_broadcast_settings(message.from_user.id)
        if settings and settings.max_delay is not None and value > settings.max_delay:
            await message.answer("Минимальная задержка не может быть больше максимальной")
            return
        await db.create_or_update_broadcast_settings(
            message.from_user.id,
            min_delay=value
        )
        await message.answer(f"✅ Запомнил! Минимальная задержка: {value} мин")
        await state.clear()


@dp.callback_query(F.data == "max_delay")
async def max_delay_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserState.waiting_max_delay)
    settings = await db.get_broadcast_settings(query.from_user.id)
    if settings and settings.max_delay is not None:
        await query.message.reply(
            f"Текущая максимальная задержка: {settings.max_delay} мин\n"
            "Отправь новое значение (в минутах!)"
        )
    else:
        await query.message.reply("Отправь мне время максимальной задержки между сообщениями (в минутах!)")
    await query.answer()


@dp.message(UserState.waiting_max_delay)
async def process_max_delay(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer(f"Отправь мне именно число")
    else:
        value = int(message.text)
        if value < MIN_DELAY_MINUTES or value > MAX_DELAY_MINUTES:
            await message.answer(f"Введите число от {MIN_DELAY_MINUTES} до {MAX_DELAY_MINUTES}")
            return
        settings = await db.get_broadcast_settings(message.from_user.id)
        if settings and settings.min_delay is not None and value < settings.min_delay:
            await message.answer("Максимальная задержка не может быть меньше минимальной")
            return
        await db.create_or_update_broadcast_settings(
            message.from_user.id,
            max_delay=value
        )
        await message.answer(f"✅ Запомнил! Максимальная задержка: {value} мин")
        await state.clear()


@dp.callback_query(F.data == "text")
async def text_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserState.waiting_text)
    settings = await db.get_broadcast_settings(query.from_user.id)
    if settings and (settings.text or settings.file_id):
        if settings.text:
            preview = settings.text
        elif settings.file_id:
            preview = "медиа"
        else:
            preview = "—"
        await query.message.reply(
            f"Текущий текст/медиа: {preview}\n"
            "Отправьте новый текст или медиа, которое я буду рассылать"
        )
    else:
        await query.message.reply("Отправьте текст или медиа, которое я буду рассылать")
    await query.answer()


@dp.message(UserState.waiting_text)
async def process_text(message: Message, state: FSMContext):
    temp_file_id, temp_media_type = _get_media_info(message)
    
    if temp_file_id is None:
        text_entities = _extract_custom_emoji_entities(message.entities)
        await db.create_or_update_broadcast_settings(
            message.from_user.id,
            text=message.text,
            file_id=None,
            media_type=None,
            caption=None,
            text_entities=text_entities,
            caption_entities=None,
        )
        await message.answer(f"✅ Текст сохранен!")
    else:
        caption_entities = _extract_custom_emoji_entities(message.caption_entities)
        await db.create_or_update_broadcast_settings(
            message.from_user.id,
            text=None,
            file_id=temp_file_id,
            media_type=temp_media_type,
            caption=message.caption,
            text_entities=None,
            caption_entities=caption_entities,
        )
        await message.answer(f"✅ Медиа сохранено!")
    
    await state.clear()


@dp.callback_query(F.data == "cancel")
async def cancel_handler(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌ Отменено")
    await query.answer()



#@dp.message(F.text == "Профиль")
#async def profile_panel_handler(message: Message):





# ==================== АДМИН КОМАНДЫ ====================

@dp.message(F.text == "⚙️ Админ панель")
async def admin_panel_handler(message: Message):
    """Показать админ панель"""
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        return
    
    builder = InlineKeyboardBuilder()
    #builder.row(InlineKeyboardButton(text="🔑 Установить API_ID/API_HASH", callback_data="admin_set_api"))
    builder.row(InlineKeyboardButton(text="👤 Назначить администратора", callback_data="admin_set_admin"))
    builder.row(InlineKeyboardButton(text="📊 Текущие настройки", callback_data="admin_view_settings"))
    builder.row(InlineKeyboardButton(text="💸 Возврат Stars", callback_data="admin_refund_stars"))
    builder.row(InlineKeyboardButton(text="📣 Рассылка всем", callback_data="admin_broadcast_all"))
    builder.row(
        InlineKeyboardButton(text="🎁 Подарить премиум", callback_data="admin_gift_premium"),
        InlineKeyboardButton(text="🧹 Убрать премиум", callback_data="admin_remove_premium"),
    )
    builder.row(InlineKeyboardButton(text="👤 Профиль пользователя", callback_data="admin_view_profile"))
    
    await message.answer("⚙️ Админ панель:", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "admin_gift_premium")
async def admin_gift_premium_handler(query: CallbackQuery, state: FSMContext):
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return

    await query.message.answer("Введите username пользователя:")
    await state.set_state(UserState.waiting_gift_username)
    await query.answer()


@dp.callback_query(F.data == "admin_view_profile")
async def admin_view_profile_handler(query: CallbackQuery, state: FSMContext):
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    await query.message.answer("Введите username пользователя для просмотра профиля:")
    await state.set_state(UserState.waiting_admin_view_profile_username)
    await query.answer()


@dp.callback_query(F.data == "admin_remove_premium")
async def admin_remove_premium_handler(query: CallbackQuery, state: FSMContext):
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    await query.message.answer("Введите username пользователя для снятия премиума:")
    await state.set_state(UserState.waiting_remove_premium_username)
    await query.answer()


@dp.message(UserState.waiting_gift_username)
async def admin_gift_username_handler(message: Message, state: FSMContext):
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return

    if not message.text:
        await message.answer("Отправьте username текстом")
        return

    username = message.text
    if not username:
        await message.answer("Username не может быть пустым")
        return

    await state.update_data(gift_username=username)
    await message.answer("Выберите план:", reply_markup=_build_gift_plans_keyboard())
    await state.set_state(UserState.waiting_gift_plan)


@dp.message(UserState.waiting_remove_premium_username)
async def admin_remove_premium_username_handler(message: Message, state: FSMContext):
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return

    if not message.text:
        await message.answer("Отправьте username текстом")
        return

    username = message.text.strip()
    if not username:
        await message.answer("Username не может быть пустым")
        return

    user = await db.get_user_by_username(username)
    if not user:
        await message.answer("❌ Пользователь не найден. Он должен хотя бы раз написать боту.")
        await state.clear()
        return

    await db.clear_premium_data(user.telegram_id)
    admin_username = message.from_user.username or "админ"
    await message.answer(f"✅ Премиум полностью убран у @{user.username or username.lstrip('@')}.")
    try:
        await message.bot.send_message(
            user.telegram_id,
            f"ℹ️ Админ @{admin_username} убрал ваш премиум.",
        )
    except Exception as e:
        logging.warning(f"Failed to notify user {user.telegram_id} about premium removal: {e}")
    await state.clear()


@dp.message(UserState.waiting_admin_view_profile_username)
async def admin_view_profile_username_handler(message: Message, state: FSMContext):
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return

    if not message.text:
        await message.answer("Отправьте username текстом")
        return

    username = message.text.strip()
    if not username:
        await message.answer("Username не может быть пустым")
        return

    user = await db.get_user_by_username(username)
    if not user:
        await message.answer("❌ Пользователь не найден. Он должен хотя бы раз написать боту.")
        await state.clear()
        return

    profile_text = await _build_profile_text(user)
    await message.answer(profile_text)
    await state.clear()


@dp.callback_query(F.data == "admin_gift_cancel")
async def admin_gift_cancel_handler(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌ Отменено")
    await query.answer()


@dp.callback_query(F.data.startswith("admin_gift_plan_"))
async def admin_gift_plan_handler(query: CallbackQuery, state: FSMContext):
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return

    plan_key = query.data.replace("admin_gift_plan_", "")
    plan = PREMIUM_PLANS.get(plan_key)
    if not plan:
        await query.answer("Неизвестный план", show_alert=True)
        return

    data = await state.get_data()
    username = data.get("gift_username")
    if not username:
        await query.answer("Сначала укажите username", show_alert=True)
        return

    user = await db.get_user_by_username(username)
    if not user:
        await query.message.answer("❌ Пользователь не найден. Он должен хотя бы раз написать боту.")
        await state.clear()
        await query.answer()
        return

    admin_username = query.from_user.username or "админ"
    await db.set_premium(user.telegram_id, plan_key, plan["days"], gift_from_username=admin_username)
    await query.message.answer(f"✅ Премиум подарен @{username} на {plan['label']}")
    await query.message.answer(f"ℹ️ Вы подарили премиум пользователю {username}.")
    bot = query.message.bot
    await bot.send_message(
        user.telegram_id,
        f"🎁 Админ @{admin_username} подарил вам премиум на {plan['label']}.",
    )
    await state.clear()
    await query.answer()


@dp.callback_query(F.data == "admin_broadcast_all")
async def admin_broadcast_all_handler(query: CallbackQuery, state: FSMContext):
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return

    await query.message.answer("📣 Отправьте сообщение для рассылки всем пользователям бота:")
    await state.set_state(UserState.waiting_admin_broadcast)
    await query.answer()


@dp.message(UserState.waiting_admin_broadcast)
async def process_admin_broadcast(message: Message, state: FSMContext):
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return

    user_ids = await db.get_all_user_ids()
    if not user_ids:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return

    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await _send_broadcast_message(message.bot, user_id, message)
            sent += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Failed to broadcast to {user_id}: {e}")
        await asyncio.sleep(0.05)

    await message.answer(f"✅ Рассылка завершена. Успешно: {sent}, ошибок: {failed}")
    await state.clear()

'''
@dp.callback_query(F.data == "admin_set_api")
async def admin_set_api_handler(query: CallbackQuery, state: FSMContext):
    """Начать процесс установки API_ID/API_HASH"""
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    await query.message.answer(
        "🔑 Установка API_ID и API_HASH\n\n"
        "1. Перейдите на https://my.telegram.org/apps\n"
        "2. Войдите в свой аккаунт\n"
        "3. Создайте приложение (если еще не создано)\n"
        "4. Скопируйте API_ID и API_HASH\n\n"
        "ввeдite API_ID (только число):"
    )
    await state.set_state(UserState.waiting_api_id)
    await query.answer()


@dp.message(UserState.waiting_api_id)
async def process_api_id(message: Message, state: FSMContext):
    """Обработка ввода API_ID"""
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return
    
    try:
        api_id = int(message.text.strip())
        await state.update_data(api_id=api_id)
        await message.answer(f"✅ API_ID сохранен: {api_id}\n\nТеперь ввeдite API_HASH:")
        await state.set_state(UserState.waiting_api_hash)
    except ValueError:
        await message.answer("❌ API_ID должен быть числом. Попробуйте снова:")


@dp.message(UserState.waiting_api_hash)
async def process_api_hash(message: Message, state: FSMContext):
    """Обработка ввода API_HASH"""
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return
    
    data = await state.get_data()
    api_id = data.get('api_id')
    api_hash = message.text.strip()
    
    if not api_hash or len(api_hash) < 10:
        await message.answer("❌ API_HASH слишком короткий. Попробуйте снова:")
        return
    
    try:
        # Сохраняем в БД
        await db.set_app_setting("api_id", str(api_id))
        await db.set_app_setting("api_hash", api_hash)
        
        # Обновляем глобальные переменные
        global API_ID, API_HASH
        API_ID = api_id
        API_HASH = api_hash
        
        await message.answer(
            f"✅ API_ID и API_HASH успешно установлены!\n\n"
            f"API_ID: {api_id}\n"
            f"API_HASH: {api_hash[:10]}...\n\n"
            f"Настройки применены. Перезапустите бота для полного применения изменений."
        )
        await state.clear()
        logging.info(f"API credentials updated by admin {message.from_user.id}")
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении: {e}")
        await state.clear()
'''

@dp.callback_query(F.data == "admin_set_admin")
async def admin_set_admin_handler(query: CallbackQuery, state: FSMContext):
    """Начать процесс назначения администратора"""
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    await query.message.answer(
        "👤 Назначение администратора\n\n"
        "Отправьте Telegram ID пользователя, которого хотите сделать администратором.\n"
        "Чтобы узнать ID, используйте @userinfobot или отправьте /start этому боту."
    )
    await state.set_state(UserState.waiting_admin_id)
    await query.answer()


@dp.message(UserState.waiting_admin_id)
async def process_admin_id(message: Message, state: FSMContext):
    """Обработка ввода ID администратора"""
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return
    
    try:
        admin_id = int(message.text.strip())
        await db.set_admin(admin_id, True)
        await message.answer(f"✅ Пользователь {admin_id} назначен администратором")
        await state.clear()
    except ValueError:
        await message.answer("❌ ID должен быть числом. Попробуйте снова:")


@dp.callback_query(F.data == "admin_view_settings")
async def admin_view_settings_handler(query: CallbackQuery):
    """Показать текущие настройки"""
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    api_id = await db.get_app_setting("api_id")
    api_hash = await db.get_app_setting("api_hash")
    
    if api_id and api_hash:
        await query.message.answer(
            f"📊 Текущие настройки:\n\n"
            f"API_ID: {api_id}\n"
            f"API_HASH: {api_hash[:10]}...\n\n"
            f"Статус: ✅ Установлены"
        )
    else:
        await query.message.answer(
            "📊 Текущие настройки:\n\n"
            "API_ID: ❌ Не установлен\n"
            "API_HASH: ❌ Не установлен\n\n"
            "Используйте кнопку 'Установить API_ID/API_HASH' для настройки."
        )
    await query.answer()


@dp.callback_query(F.data == "admin_refund_stars")
async def admin_refund_stars_handler(query: CallbackQuery, state: FSMContext):
    is_admin = await db.is_admin(query.from_user.id)
    if not is_admin:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return

    await query.message.answer(
        "💸 Возврат Stars\n\n"
        "Отправьте:\n"
        "1) Telegram ID пользователя и telegram_payment_charge_id через пробел\n"
        "или\n"
        "2) Только Telegram ID (будет использован последний платеж)\n\n"
        "Пример: 7086557635 1234567890abcdef"
    )
    await state.set_state(UserState.waiting_refund_data)
    await query.answer()


@dp.message(UserState.waiting_refund_data)
async def process_refund_data(message: Message, state: FSMContext):
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("❌ У вас нет прав администратора")
        await state.clear()
        return

    if not message.text:
        await message.answer("Отправьте данные текстом")
        return

    parts = message.text.strip().split()
    if len(parts) not in (1, 2):
        await message.answer("Неверный формат. Отправьте: user_id [telegram_payment_charge_id]")
        return

    try:
        target_user_id = int(parts[0])
    except ValueError:
        await message.answer("user_id должен быть числом")
        return

    charge_id = parts[1] if len(parts) == 2 else None
    if not charge_id:
        user = await db.get_user(target_user_id)
        if not user or not user.last_payment_charge_id:
            await message.answer("Не найден последний платеж для этого пользователя")
            return
        charge_id = user.last_payment_charge_id

    bot = BOT or Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        ok = await bot.refund_star_payment(user_id=target_user_id, telegram_payment_charge_id=charge_id)
        if ok:
            await db.revoke_premium(target_user_id)
            await message.answer("✅ Возврат Stars выполнен, премиум отключен.")
        else:
            await message.answer("❌ Не удалось выполнить возврат.")
    except Exception as e:
        await message.answer(f"❌ Ошибка возврата: {e}")
    finally:
        await state.clear()


async def main() -> None:
    try:
        # Инициализация БД
        await db.init_db()
        logging.info("Database initialized")

        # Загружаем API credentials
        await load_api_credentials()

        if not API_ID or not API_HASH:
            logging.warning("⚠️ API_ID и API_HASH не установлены! Администратор должен использовать админ панель")

        # Устанавливаем первого администратора из .env (если указан)
        admin_id = getenv("ADMIN_ID")
        if admin_id:
            try:
                admin_id_int = int(admin_id)
                await db.set_admin(admin_id_int, True)
                logging.info(f"Admin {admin_id_int} set from .env")
            except ValueError:
                logging.warning(f"Invalid ADMIN_ID in .env: {admin_id}")

        global BOT
        BOT = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        await dp.start_polling(BOT)
    finally:
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("telethon").setLevel(logging.DEBUG)
    logging.getLogger("cryptobot-python").setLevel(logging.INFO)
    asyncio.run(main())

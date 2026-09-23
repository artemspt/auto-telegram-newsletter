import logging
import time

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneCodeEmptyError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    FloodWaitError,
)

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from aiogram.fsm.context import FSMContext


AUTH_TTL_SECONDS = 10 * 60
MAX_CODE_ATTEMPTS = 5
MAX_PASSWORD_ATTEMPTS = 5

# user_id -> (клиент, время начала авторизации)
active_auth_clients: dict[int, tuple[TelegramClient, float]] = {}


def _is_auth_expired(code_requested_at: float) -> bool:
    return (time.time() - code_requested_at) > AUTH_TTL_SECONDS


async def _drop_auth_client(user_id: int):
    entry = active_auth_clients.pop(user_id, None)
    if entry:
        await entry[0].disconnect()


def register_auth_handlers(dp, UserState, db, api_id, api_hash, show_broadcast_menu):
    async def finish_auth(user_id, state):
        """Завершить процесс авторизации"""
        await _drop_auth_client(user_id)
        await state.clear()

    async def fail(message: Message, state: FSMContext, text: str):
        """Сообщить об ошибке и прервать авторизацию"""
        await message.answer(text, parse_mode=None)
        await finish_auth(message.from_user.id, state)

    async def complete_auth(message: Message, state: FSMContext, client: TelegramClient, phone: str):
        """Сохранить сессию и показать меню рассылки"""
        user = message.from_user
        # Пользователь должен существовать в БД: на него ссылается сессия
        await db.get_or_create_user(user.id, user.username, user.full_name)
        await db.save_session(user.id, client.session.save(), phone)
        await finish_auth(user.id, state)
        await show_broadcast_menu(message, user.id)

    async def try_sign_in_with_code(message: Message, state: FSMContext, code: str):
        """Попытка входа с k0dом"""
        user_id = message.from_user.id
        entry = active_auth_clients.get(user_id)

        if not entry:
            await fail(message, state, "Сессия потеряна. Начните заново через /start")
            return

        client = entry[0]
        # Проверяем подключение клиента
        if not client.is_connected():
            logging.warning(f"Client disconnected for user {user_id}, reconnecting...")
            try:
                await client.connect()
            except Exception as e:
                logging.error(f"Failed to reconnect client for user {user_id}: {e}")
                await fail(message, state, "❌ Ошибка подключения. Начните @вторизацiю заново через /start")
                return

        current_data = await state.get_data()
        phone_code_hash = current_data.get('phone_code_hash')
        phone_number = current_data.get('phone')
        code_requested_at = current_data.get('code_requested_at')

        if not phone_code_hash or not phone_number or not code_requested_at:
            await fail(message, state, "❌ Ошибка: данные сессии повреждены. Начните заново через /start")
            return

        if _is_auth_expired(code_requested_at):
            await fail(message, state, "⏰ Время ожидания истекло. Начните авторизацию заново через /start")
            return

        try:
            logging.info(f"Attempting sign_in for user {user_id}")
            await client.sign_in(
                phone=phone_number,
                code=code,
                phone_code_hash=phone_code_hash
            )

        except SessionPasswordNeededError:
            logging.info(f"SessionPasswordNeededError for user {user_id} - requesting 2FA password")
            await message.answer("🔐 ввeдite п@р0lь двухф@кт0рн0й аутентифiкации:")
            await state.update_data(password_attempts=0)
            await state.set_state(UserState.wait_password)
            return

        except PhoneCodeExpiredError:
            logging.warning(f"PhoneCodeExpiredError for user {user_id} - code expired")
            await fail(
                message,
                state,
                "⏰ k0d подтверждения истек.\n\n"
                "⚠️ Это может произойти если:\n"
                "• k0d был введен слишком поздно\n"
                "• k0d был отправлен текстом в бот (Telegram отзывает такие k0dы)\n\n"
                "💡 Рекомендации:\n"
                "• Начните @вторизацiю заново через /start\n"
                "• ввeдite k0d СРАЗУ после получения (в течение 1-2 минут)\n"
                "• Не отправляйте k0d текстом",
            )
            return

        except (PhoneCodeInvalidError, PhoneCodeEmptyError):
            logging.warning(f"Invalid code for user {user_id}")
            attempts = (current_data.get("code_attempts") or 0) + 1
            if attempts >= MAX_CODE_ATTEMPTS:
                await fail(message, state, "❌ Превышено количество попыток. Начните заново через /start")
                return
            # Сбрасываем набранные цифры, иначе следующая допишется к неверному k0dу
            await state.update_data(code_attempts=attempts, entered_code="")
            await message.answer("❌ Неправильный k0d. Отправьте k0d заново, по одной цифре в сообщении:")
            return

        except FloodWaitError as e:
            logging.warning(f"FloodWaitError for user {user_id}: wait {e.seconds} seconds")
            await fail(message, state, f"⏳ Слишком много попыток. Подождите {e.seconds} секунд и попробуйте снова.")
            return

        except Exception as e:
            logging.error(f"Error in try_sign_in_with_code for user {user_id}: {e}", exc_info=True)
            await fail(message, state, f"❌ Ошибка: {e}")
            return

        logging.info(f"Sign_in successful for user {user_id}")
        await message.answer("✅ Вход выполнен! Теперь вы можете использовать рассылку.")
        await complete_auth(message, state, client, phone_number)

    @dp.message(UserState.wait_phone)
    async def process_phone(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id

        # Проверяем, что сообщение содержит текст
        if not message.text:
            await message.answer("Пожалуйста, отправьте номер телефона текстом (в формате +79991234567)")
            return

        phone = message.text.strip()

        # Проверяем формат номера
        if not phone.startswith('+'):
            await message.answer("Номер телефона должен начинаться с + (например, +79991234567)")
            return

        # Закрываем прошлую попытку этого пользователя и брошенные попытки остальных
        # ponytail: чистка только при новых попытках входа; нужен фоновый таймер, если брошенных станет много
        for uid, (_, started_at) in list(active_auth_clients.items()):
            if uid == user_id or _is_auth_expired(started_at):
                await _drop_auth_client(uid)

        # Создаем клиент с правильными параметрами устройства
        client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            device_model="Desktop",
            system_version="1.0",
            app_version="1.0",
            lang_code="en",
            system_lang_code="en"
        )

        try:
            await client.connect()
            sent_code = await client.send_code_request(phone)
            code_length = getattr(getattr(sent_code, "type", None), "length", None)
            active_auth_clients[user_id] = (client, time.time())
            await state.update_data(
                phone=phone,
                phone_code_hash=sent_code.phone_code_hash,
                code_requested_at=time.time(),
                code_attempts=0,
                entered_code="",
                code_length=code_length,
            )
            await message.answer(
                "✅ k0d отправлен на ваш телефон.\n"
                "Отправляйте k0d по одной цифре в сообщении."
            )
            await state.set_state(UserState.wait_code)
            logging.info(f"Code sent for user {user_id}")

        except PhoneNumberInvalidError:
            await client.disconnect()
            await message.answer("❌ Неверный номер телефона. Проверьте формат и попробуйте снова (например, +79991234567)")

        except PhoneNumberUnoccupiedError:
            await client.disconnect()
            await message.answer("❌ Этот номер телефона не зарегистрирован в Telegram. Убедитесь, что номер правильный.")

        except FloodWaitError as e:
            await client.disconnect()
            logging.warning(f"FloodWaitError when requesting code for user {user_id}: wait {e.seconds} seconds")
            await message.answer(f"⏳ Слишком много запросов. Подождите {e.seconds} секунд и попробуйте снова.")

        except Exception as e:
            await client.disconnect()
            logging.error(f"Error sending code request for user {user_id}: {e}", exc_info=True)
            await message.answer(f"❌ Ошибка при отправке k0dа: {e}", parse_mode=None)

    @dp.message(UserState.wait_code)
    async def process_code(message: Message, state: FSMContext):
        """Обработка текстового ввода k0dа"""
        digit = (message.text or "").strip()
        if not digit.isdecimal() or len(digit) != 1:
            await message.answer("❌ Отправьте одну цифру k0dа")
            return

        data = await state.get_data()
        entered_code = data.get("entered_code", "") + digit
        code_length = data.get("code_length") or 5
        await state.update_data(entered_code=entered_code)

        if len(entered_code) < code_length:
            await message.answer(f"Принято {len(entered_code)}/{code_length}. Продолжайте.")
            return

        await try_sign_in_with_code(message, state, entered_code)

    @dp.message(UserState.wait_password)
    async def process_password(message: Message, state: FSMContext):
        user_id = message.from_user.id
        entry = active_auth_clients.get(user_id)

        if not entry:
            await fail(message, state, "❌ Сессия потеряна. Начните заново через /start")
            return

        if not message.text:
            await message.answer("Пожалуйста, отправьте п@р0lь текстом")
            return

        password = message.text.strip()
        # Не оставляем п@р0lь в истории чата
        try:
            await message.delete()
        except TelegramBadRequest:
            pass

        client = entry[0]
        data = await state.get_data()
        try:
            logging.info(f"Attempting sign_in with password for user {user_id}")
            await client.sign_in(password=password)

        except PasswordHashInvalidError:
            logging.warning(f"Wrong password for user {user_id}")
            attempts = (data.get("password_attempts") or 0) + 1
            if attempts >= MAX_PASSWORD_ATTEMPTS:
                await fail(message, state, "❌ Превышено количество попыток. Начните заново через /start")
                return
            await state.update_data(password_attempts=attempts)
            await message.answer("❌ Неправильный п@р0lь. ввeдite п@р0lь еще раз:")
            return

        except FloodWaitError as e:
            logging.warning(f"FloodWaitError on password for user {user_id}: wait {e.seconds} seconds")
            await fail(message, state, f"⏳ Слишком много попыток. Подождите {e.seconds} секунд и попробуйте снова.")
            return

        except Exception as e:
            logging.error(f"Error in process_password for user {user_id}: {e}", exc_info=True)
            await fail(message, state, f"❌ Ошибка: {e}")
            return

        logging.info(f"Sign_in with password successful for user {user_id}")
        await message.answer("✅ Вход по 2FA выполнен! Теперь вы можете использовать рассылку.")
        await complete_auth(message, state, client, data.get("phone"))

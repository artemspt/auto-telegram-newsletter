import logging
import time

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneCodeEmptyError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    FloodWaitError,
)

from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext


AUTH_TTL_SECONDS = 10 * 60
MAX_CODE_ATTEMPTS = 5
MAX_PASSWORD_ATTEMPTS = 5

active_auth_clients = {}


def _is_auth_expired(code_requested_at: float) -> bool:
    return (time.time() - code_requested_at) > AUTH_TTL_SECONDS


def register_auth_handlers(dp, UserState, db, get_api_credentials, show_broadcast_menu):
    async def finish_auth(user_id, state):
        """Завершить процесс авторизации"""
        client = active_auth_clients.pop(user_id, None)
        if client:
            await client.disconnect()
        await state.clear()

    async def try_sign_in_with_code(user_id: int, code: str, state: FSMContext, query: CallbackQuery = None, message: Message = None):
        """Попытка входа с k0dом (используется и для inline, и для текстового ввода)"""
        # Убеждаемся, что пользователь существует в БД
        if query:
            await db.get_or_create_user(
                user_id,
                query.from_user.username,
                query.from_user.full_name or query.from_user.first_name
            )
        elif message:
            await db.get_or_create_user(
                user_id,
                message.from_user.username,
                message.from_user.full_name or message.from_user.first_name
            )

        client = active_auth_clients.get(user_id)

        if not client:
            msg = "Сессия потеряна. Начните заново через /start"
            if query:
                await query.answer(msg, show_alert=True)
            elif message:
                await message.answer(msg)
            return False

        # Проверяем подключение клиента
        if not client.is_connected():
            logging.warning(f"Client disconnected for user {user_id}, reconnecting...")
            try:
                await client.connect()
            except Exception as e:
                logging.error(f"Failed to reconnect client for user {user_id}: {e}")
                msg = "❌ Ошибка подключения. Начните @вторизацiю заново через /start"
                if query:
                    await query.answer(msg, show_alert=True)
                elif message:
                    await message.answer(msg)
                await finish_auth(user_id, state)
                return False

        current_data = await state.get_data()
        phone_code_hash = current_data.get('phone_code_hash')
        phone_number = current_data.get('phone')
        code_requested_at = current_data.get('code_requested_at')

        if not phone_code_hash or not phone_number or not code_requested_at:
            msg = "❌ Ошибка: данные сессии повреждены. Начните заново через /start"
            if query:
                await query.answer(msg, show_alert=True)
            elif message:
                await message.answer(msg)
            await finish_auth(user_id, state)
            return False

        if _is_auth_expired(code_requested_at):
            msg = "⏰ Время ожидания истекло. Начните авторизацию заново через /start"
            if query:
                await query.answer(msg, show_alert=True)
            elif message:
                await message.answer(msg)
            await finish_auth(user_id, state)
            return False

        try:
            logging.info(f"Attempting sign_in for user {user_id}. Phone: {phone_number}")

            result = await client.sign_in(
                phone=phone_number,
                code=code,
                phone_code_hash=phone_code_hash
            )

            logging.info(f"Sign_in successful for user {user_id}. Result type: {type(result)}")

            # Успех - сохраняем сессию
            final_session = client.session.save()
            await db.save_session(user_id, final_session, phone_number)

            success_msg = "✅ Вход выполнен! Теперь вы можете использовать рассылку."
            if query:
                await query.message.edit_text(success_msg)
                await query.answer("✅ Успешно!")
            elif message:
                await message.answer(success_msg)
            await finish_auth(user_id, state)

            # Показываем меню рассылки
            if query:
                await show_broadcast_menu(query.message, state)
            elif message:
                await show_broadcast_menu(message, state)
            return True

        except SessionPasswordNeededError:
            logging.info(f"SessionPasswordNeededError for user {user_id} - requesting 2FA password")
            msg = "🔐 ввeдite п@р0lь двухф@кт0рн0й аутентифiкации:"
            if query:
                await query.message.edit_text(msg)
                await query.answer()
            elif message:
                await message.answer(msg)
            await state.update_data(password_attempts=0)
            await state.set_state(UserState.wait_password)
            return False

        except PhoneCodeExpiredError:
            logging.warning(f"PhoneCodeExpiredError for user {user_id} - code expired")
            msg = (
                "⏰ k0d подтверждения истек.\n\n"
                "⚠️ Это может произойти если:\n"
                "• k0d был введен слишком поздно\n"
                "• k0d был отправлен текстом в бот (Telegram отзывает такие k0dы)\n\n"
                "💡 Рекомендации:\n"
                "• Начните @вторизацiю заново через /start\n"
                "• ввeдite k0d СРАЗУ после получения (в течение 1-2 минут)\n"
                "• Не отправляйте k0d текстом\n\n"
                "Попробуйте снова:"
            )
            if query:
                await query.message.edit_text(msg)
                await query.answer("⏰ k0d истек", show_alert=True)
            await finish_auth(user_id, state)
            return False

        except PhoneCodeInvalidError:
            logging.warning(f"PhoneCodeInvalidError for user {user_id}")
            attempts = (current_data.get("code_attempts") or 0) + 1
            await state.update_data(code_attempts=attempts)
            if attempts >= MAX_CODE_ATTEMPTS:
                msg = "❌ Превышено количество попыток. Начните заново через /start"
                if query:
                    await query.answer(msg, show_alert=True)
                elif message:
                    await message.answer(msg)
                await finish_auth(user_id, state)
                return False
            if query:
                await query.answer("❌ Неправильный k0d", show_alert=True)
            elif message:
                await message.answer("❌ Неправильный k0d. Попробуйте еще раз:")
            return False

        except PhoneCodeEmptyError:
            logging.warning(f"PhoneCodeEmptyError for user {user_id}")
            attempts = (current_data.get("code_attempts") or 0) + 1
            await state.update_data(code_attempts=attempts)
            if attempts >= MAX_CODE_ATTEMPTS:
                msg = "❌ Превышено количество попыток. Начните заново через /start"
                if query:
                    await query.answer(msg, show_alert=True)
                elif message:
                    await message.answer(msg)
                await finish_auth(user_id, state)
                return False
            if query:
                await query.answer("❌ k0d пустой", show_alert=True)
            elif message:
                await message.answer("❌ k0d не может быть пустым. Попробуйте еще раз:")
            return False

        except FloodWaitError as e:
            wait_time = e.seconds
            logging.warning(f"FloodWaitError for user {user_id}: wait {wait_time} seconds")
            msg = f"⏳ Слишком много попыток. Подождите {wait_time} секунд и попробуйте снова."
            if query:
                await query.answer(msg, show_alert=True)
            elif message:
                await message.answer(msg)
            await finish_auth(user_id, state)
            return False

        except Exception as e:
            error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
            logging.error(f"Error in try_sign_in_with_code for user {user_id}: {e}", exc_info=True)
            msg = f"❌ Ошибка: {error_msg}"
            if query:
                await query.answer(msg, show_alert=True)
            elif message:
                await message.answer(msg, parse_mode=None)
            await finish_auth(user_id, state)
            return False

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

        api_id, api_hash = get_api_credentials()
        if not api_id or not api_hash:
            await message.answer("❌ API_ID и API_HASH не настроены. Обратитесь к администратору.")
            return

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
        await client.connect()
        logging.info(f"Client created and connected for user {user_id}")

        try:
            sent_code = await client.send_code_request(phone)
            phone_code_hash = sent_code.phone_code_hash
            code_length = getattr(getattr(sent_code, "type", None), "length", None)
            active_auth_clients[user_id] = client
            await state.update_data(
                phone=phone,
                phone_code_hash=phone_code_hash,
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
            logging.info(f"Code sent to phone {phone} for user {user_id}.")

        except PhoneNumberInvalidError:
            await client.disconnect()
            await message.answer("❌ Неверный номер телефона. Проверьте формат и попробуйте снова (например, +79991234567)")

        except PhoneNumberUnoccupiedError:
            await client.disconnect()
            await message.answer("❌ Этот номер телефона не зарегистрирован в Telegram. Убедитесь, что номер правильный.")

        except FloodWaitError as e:
            await client.disconnect()
            wait_time = e.seconds
            logging.warning(f"FloodWaitError when requesting code for user {user_id}: wait {wait_time} seconds")
            await message.answer(f"⏳ Слишком много запросов. Подождите {wait_time} секунд и попробуйте снова.")

        except Exception as e:
            await client.disconnect()
            # Используем parse_mode=None чтобы избежать проблем с HTML-тегами в сообщении об ошибке
            error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
            logging.error(f"Error sending code request for user {user_id}: {e}", exc_info=True)
            await message.answer(f"❌ Ошибка при отправке k0dа: {error_msg}", parse_mode=None)

    @dp.message(UserState.wait_code)
    async def process_code(message: Message, state: FSMContext):
        """Обработка текстового ввода k0dа"""
        if not message.text:
            await message.answer("Отправьте одну цифру k0dа")
            return

        digit = message.text.strip()
        if not digit.isdigit() or len(digit) != 1:
            await message.answer("❌ Отправьте одну цифру k0dа")
            return

        data = await state.get_data()
        entered_code = data.get("entered_code", "")
        code_length = data.get("code_length") or 5
        entered_code += digit
        await state.update_data(entered_code=entered_code)

        if len(entered_code) < code_length:
            await message.answer(f"Принято {len(entered_code)}/{code_length}. Продолжайте.")
            return

        # Используем общую функцию для входа
        await try_sign_in_with_code(message.from_user.id, entered_code, state, message=message)

    @dp.message(UserState.wait_password)
    async def process_password(message: Message, state: FSMContext):
        user_id = message.from_user.id
        client = active_auth_clients.get(user_id)

        if not client:
            await message.answer("❌ Сессия потеряна. Начните заново через /start")
            await finish_auth(user_id, state)
            return

        if not message.text:
            await message.answer("Пожалуйста, отправьте п@р0lь текстом")
            return

        password = message.text.strip()

        try:
            logging.info(f"Attempting sign_in with password for user {user_id}")
            result = await client.sign_in(password=password)
            logging.info(f"Sign_in with password successful for user {user_id}")

            final_session = client.session.save()

            # Убеждаемся, что пользователь существует в БД
            await db.get_or_create_user(
                user_id,
                message.from_user.username,
                message.from_user.full_name or message.from_user.first_name
            )

            # Сохраняем сессию в БД
            data = await state.get_data()
            await db.save_session(user_id, final_session, data.get('phone'))

            await message.answer("✅ Вход по 2FA выполнен! Теперь вы можете использовать рассылку.")
            await finish_auth(user_id, state)
            await show_broadcast_menu(message, state)

        except SessionPasswordNeededError:
            # п@р0lь неправильный, но сессия еще активна
            logging.warning(f"Wrong password for user {user_id}")
            data = await state.get_data()
            attempts = (data.get("password_attempts") or 0) + 1
            await state.update_data(password_attempts=attempts)
            if attempts >= MAX_PASSWORD_ATTEMPTS:
                await message.answer("❌ Превышено количество попыток. Начните заново через /start")
                await finish_auth(user_id, state)
                return
            await message.answer("❌ Неправильный п@р0lь. ввeдite п@р0lь еще раз:")
            # НЕ завершаем @вторизацiю, позволяем попробовать снова

        except Exception as e:
            error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
            logging.error(f"Error in process_password for user {user_id}: {e}", exc_info=True)
            await message.answer(f"❌ Ошибка: {error_msg}", parse_mode=None)
            # При серьезной ошибке завершаем @вторизацiю
            await finish_auth(user_id, state)

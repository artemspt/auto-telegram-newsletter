import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, String, BigInteger, Boolean, Text, ForeignKey, JSON, DateTime, select, update, text, func
from datetime import datetime, timedelta
from os import getenv
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    
    id = Column(BigInteger, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)  # Флаг администратора
    is_premium = Column(Boolean, default=False)  # Премиум-подписка
    premium_plan = Column(String(32), nullable=True)  # План (trial, month, 3month, year)
    premium_started_at = Column(DateTime, nullable=True)  # Дата начала подписки
    premium_until = Column(DateTime, nullable=True)  # Дата окончания доступа
    last_payment_charge_id = Column(String(255), nullable=True)  # Последний платеж Telegram Stars
    trial_review_offered = Column(Boolean, default=False)  # Предложение оставить отзыв показано
    trial_review_used = Column(Boolean, default=False)  # Отзыв за триал использован
    gift_from_username = Column(String(255), nullable=True)  # Кто подарил премиум
    invited_by = Column(BigInteger, nullable=True)  # Кто пригласил
    referral_count = Column(Integer, default=0)  # Количество приглашенных
    broadcast_sent_total = Column(Integer, default=0)  # Всего отправлено сообщений
    broadcast_active_seconds = Column(Integer, default=0)  # Время активной рассылки (сек)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    broadcast_settings = relationship("BroadcastSettings", back_populates="user", cascade="all, delete-orphan")


class UserSession(Base):
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    session_string = Column(Text, nullable=False)
    phone = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="sessions")


class BroadcastSettings(Base):
    __tablename__ = "broadcast_settings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    min_delay = Column(Integer, default=0)  # в секундах
    max_delay = Column(Integer, default=0)  # в секундах
    text = Column(Text, nullable=True)
    file_id = Column(String(255), nullable=True)
    media_type = Column(String(50), nullable=True)
    caption = Column(Text, nullable=True)
    text_entities = Column(JSON, nullable=True)
    caption_entities = Column(JSON, nullable=True)
    
    # Настройки рассылки по папкам/чатам
    selected_folders = Column(JSON, nullable=True)  # список ID папок
    selected_chats = Column(JSON, nullable=True)  # список ID чатов
    messages_per_chat = Column(Integer, default=1)  # количество сообщений на чат
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="broadcast_settings")


class AppSettings(Base):
    """Настройки приложения (API_ID, API_HASH и т.д.)"""
    __tablename__ = "app_settings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False)  # например, "api_id", "api_hash"
    value = Column(Text, nullable=True)  # значение настройки
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Database:
    def __init__(self):
        db_url = getenv("DATABASE_URL")
        if not db_url:
            # Формат: postgresql+asyncpg://user:password@host:port/dbname
            db_user = getenv("DB_USER")
            db_password = getenv("DB_PASSWORD")
            db_host = getenv("DB_HOST", "localhost")
            db_port = getenv("DB_PORT", "5432")
            db_name = getenv("DB_NAME", "telegram_bot")
            if not db_user or not db_password:
                raise RuntimeError("DB_USER и DB_PASSWORD должны быть заданы в переменных окружения")
            db_url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        self.engine = create_async_engine(db_url, echo=False)
        self.async_session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
    
    async def init_db(self):
        """Создает все таблицы и выполняет миграции"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        # Выполняем миграции
        await self._migrate_add_is_admin_column()
        await self._migrate_add_premium_columns()
        await self._migrate_add_broadcast_entities()
        await self._migrate_add_trial_review_columns()
        await self._migrate_add_gift_from_column()
        await self._migrate_add_broadcast_stats_columns()
        await self._migrate_add_referral_columns()
    
    async def _migrate_add_is_admin_column(self):
        """Миграция: добавляет колонку is_admin если её нет"""
        async with self.engine.begin() as conn:
            try:
                # Проверяем, существует ли колонка is_admin
                result = await conn.execute(
                    text("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='users' AND column_name='is_admin'
                    """)
                )
                exists = result.scalar_one_or_none() is not None
                
                if not exists:
                    # Добавляем колонку
                    await conn.execute(
                        text("""
                            ALTER TABLE users 
                            ADD COLUMN is_admin BOOLEAN DEFAULT FALSE
                        """)
                    )
                    logging.info("Migration: Added is_admin column to users table")
                else:
                    logging.info("Migration: is_admin column already exists")
            except Exception as e:
                logging.warning(f"Migration error (may be expected if column already exists): {e}")

    async def _migrate_add_premium_columns(self):
        """Миграция: добавляет премиум-колонки если их нет"""
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='is_premium'
                    """)
                )
                is_premium_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='premium_until'
                    """)
                )
                premium_until_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='premium_plan'
                    """)
                )
                premium_plan_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='premium_started_at'
                    """)
                )
                premium_started_at_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='last_payment_charge_id'
                    """)
                )
                last_payment_charge_id_exists = result.scalar_one_or_none() is not None

                if not is_premium_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN is_premium BOOLEAN DEFAULT FALSE
                        """)
                    )
                    logging.info("Migration: Added is_premium column to users table")

                if not premium_until_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN premium_until TIMESTAMP
                        """)
                    )
                    logging.info("Migration: Added premium_until column to users table")

                if not premium_plan_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN premium_plan VARCHAR(32)
                        """)
                    )
                    logging.info("Migration: Added premium_plan column to users table")

                if not premium_started_at_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN premium_started_at TIMESTAMP
                        """)
                    )
                    logging.info("Migration: Added premium_started_at column to users table")

                if not last_payment_charge_id_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN last_payment_charge_id VARCHAR(255)
                        """)
                    )
                    logging.info("Migration: Added last_payment_charge_id column to users table")

                # Для существующих пользователей без premium_until даем 1 день пробного доступа
                await conn.execute(
                    text("""
                        UPDATE users
                        SET premium_until = created_at + INTERVAL '1 day'
                        WHERE premium_until IS NULL
                    """)
                )
                await conn.execute(
                    text("""
                        UPDATE users
                        SET premium_plan = 'trial'
                        WHERE premium_plan IS NULL
                    """)
                )
            except Exception as e:
                logging.warning(f"Migration error (may be expected if column already exists): {e}")

    async def _migrate_add_broadcast_entities(self):
        """Миграция: добавляет колонки для entities рассылки"""
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='broadcast_settings' AND column_name='text_entities'
                    """)
                )
                text_entities_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='broadcast_settings' AND column_name='caption_entities'
                    """)
                )
                caption_entities_exists = result.scalar_one_or_none() is not None

                if not text_entities_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE broadcast_settings
                            ADD COLUMN text_entities JSON
                        """)
                    )
                    logging.info("Migration: Added text_entities column to broadcast_settings table")

                if not caption_entities_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE broadcast_settings
                            ADD COLUMN caption_entities JSON
                        """)
                    )
                    logging.info("Migration: Added caption_entities column to broadcast_settings table")
            except Exception as e:
                logging.warning(f"Migration error (may be expected if column already exists): {e}")

    async def _migrate_add_trial_review_columns(self):
        """Миграция: добавляет колонки для триал-отзыва"""
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='trial_review_offered'
                    """)
                )
                offered_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='trial_review_used'
                    """)
                )
                used_exists = result.scalar_one_or_none() is not None

                if not offered_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN trial_review_offered BOOLEAN DEFAULT FALSE
                        """)
                    )
                    logging.info("Migration: Added trial_review_offered column to users table")

                if not used_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN trial_review_used BOOLEAN DEFAULT FALSE
                        """)
                    )
                    logging.info("Migration: Added trial_review_used column to users table")
            except Exception as e:
                logging.warning(f"Migration error (may be expected if column already exists): {e}")

    async def _migrate_add_gift_from_column(self):
        """Миграция: добавляет колонку gift_from_username"""
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='gift_from_username'
                    """)
                )
                exists = result.scalar_one_or_none() is not None
                if not exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN gift_from_username VARCHAR(255)
                        """)
                    )
                    logging.info("Migration: Added gift_from_username column to users table")
            except Exception as e:
                logging.warning(f"Migration error (may be expected if column already exists): {e}")

    async def _migrate_add_broadcast_stats_columns(self):
        """Миграция: добавляет колонки статистики рассылки"""
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='broadcast_sent_total'
                    """)
                )
                sent_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='broadcast_active_seconds'
                    """)
                )
                time_exists = result.scalar_one_or_none() is not None

                if not sent_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN broadcast_sent_total INTEGER DEFAULT 0
                        """)
                    )
                    logging.info("Migration: Added broadcast_sent_total column to users table")

                if not time_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN broadcast_active_seconds INTEGER DEFAULT 0
                        """)
                    )
                    logging.info("Migration: Added broadcast_active_seconds column to users table")
            except Exception as e:
                logging.warning(f"Migration error (may be expected if column already exists): {e}")

    async def _migrate_add_referral_columns(self):
        """Миграция: добавляет колонки рефералов"""
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='invited_by'
                    """)
                )
                invited_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='referral_count'
                    """)
                )
                count_exists = result.scalar_one_or_none() is not None

                if not invited_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN invited_by BIGINT
                        """)
                    )
                    logging.info("Migration: Added invited_by column to users table")

                if not count_exists:
                    await conn.execute(
                        text("""
                            ALTER TABLE users
                            ADD COLUMN referral_count INTEGER DEFAULT 0
                        """)
                    )
                    logging.info("Migration: Added referral_count column to users table")
            except Exception as e:
                logging.warning(f"Migration error (may be expected if column already exists): {e}")
    
    async def close(self):
        """Закрывает соединение с БД"""
        await self.engine.dispose()
    
    async def get_user(self, telegram_id: int) -> User:
        """Получить пользователя по telegram_id"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            return result.scalar_one_or_none()

    async def get_user_by_username(self, username: str) -> User:
        """Получить пользователя по username (без @)"""
        if not username:
            return None
        normalized = username.lstrip("@").lower()
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(func.lower(User.username) == normalized)
            )
            return result.scalar_one_or_none()
    
    async def create_user(
        self,
        telegram_id: int,
        username: str = None,
        full_name: str = None,
        invited_by: int | None = None,
    ) -> User:
        """Создать нового пользователя"""
        async with self.async_session() as session:
            now = datetime.utcnow()
            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                invited_by=invited_by,
                is_premium=False,
                premium_plan="trial",
                premium_started_at=None,
                premium_until=now + timedelta(days=1)
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    
    async def get_or_create_user(self, telegram_id: int, username: str = None, full_name: str = None) -> User:
        """Получить пользователя или создать нового"""
        user = await self.get_user(telegram_id)
        if not user:
            user = await self.create_user(telegram_id, username, full_name)
        elif user.premium_until is None or user.premium_started_at is None or user.premium_plan is None:
            if (
                user.premium_until is None
                and user.premium_started_at is None
                and user.premium_plan is None
                and not user.is_premium
            ):
                return user
            async with self.async_session() as session:
                result = await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                user = result.scalar_one_or_none()
                if user:
                    now = datetime.utcnow()
                    if user.premium_until is None:
                        user.premium_until = now + timedelta(days=1)
                    if user.premium_plan is None:
                        user.premium_plan = "trial"
                    if user.premium_started_at is None and user.is_premium and user.premium_plan != "trial":
                        user.premium_started_at = user.created_at or now
                    await session.commit()
        return user

    async def update_user_info(self, telegram_id: int, username: str = None, full_name: str = None):
        """Обновить username/full_name пользователя"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            if username:
                user.username = username.lstrip("@")
            if full_name:
                user.full_name = full_name
            await session.commit()

    async def set_trial_review_offered(self, telegram_id: int):
        """Отметить, что пользователю показали предложение оставить отзыв"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            user.trial_review_offered = True
            await session.commit()

    async def set_trial_review_used(self, telegram_id: int):
        """Отметить, что пользователь уже использовал отзыв за триал"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            user.trial_review_used = True
            await session.commit()

    async def set_premium(
        self,
        telegram_id: int,
        plan: str,
        duration_days: int,
        start_at: datetime = None,
        gift_from_username: str | None = None,
    ):
        """Установить премиум-статус пользователя по плану"""
        status = await db.get_premium_status(telegram_id)
        is_premium_active = bool(status and status["remaining"] and status["remaining"].total_seconds() > 0)
        start_at = start_at or datetime.utcnow()
        premium_until = start_at + timedelta(days=duration_days)
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                user = User(
                    telegram_id=telegram_id,
                    is_premium=True,
                    premium_plan=plan,
                    premium_started_at=start_at,
                    premium_until=premium_until,
                    gift_from_username=gift_from_username,
                    created_at=datetime.utcnow(),
                )
                session.add(user)

            elif is_premium_active:
                base_until = user.premium_until or start_at
                user.is_premium = True
                user.premium_plan = plan
                user.premium_started_at = user.premium_started_at or start_at
                user.premium_until = base_until + timedelta(days=duration_days)
                user.gift_from_username = gift_from_username

            else:
                user.is_premium = True
                user.premium_plan = plan
                user.premium_started_at = start_at
                user.premium_until = premium_until
                user.gift_from_username = gift_from_username
            await session.commit()

    async def increment_broadcast_stats(self, telegram_id: int, sent_inc: int = 0, active_seconds_inc: int = 0):
        """Увеличить статистику рассылки пользователя"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            if sent_inc:
                user.broadcast_sent_total = (user.broadcast_sent_total or 0) + sent_inc
            if active_seconds_inc:
                user.broadcast_active_seconds = (user.broadcast_active_seconds or 0) + active_seconds_inc
            await session.commit()

    async def add_trial_days(self, telegram_id: int, days: int = 1):
        """Добавить дни триала пользователю"""
        if days <= 0:
            return
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            now = datetime.utcnow()
            base_until = user.premium_until if user.premium_until and user.premium_until > now else now
            user.premium_until = base_until + timedelta(days=days)
            user.is_premium = True
            if not user.premium_plan:
                user.premium_plan = "trial"
            if not user.premium_started_at:
                user.premium_started_at = now
            await session.commit()

    async def increment_referral(self, telegram_id: int, days: int = 1):
        """Увеличить счетчик рефералов и начислить дни"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            user.referral_count = (user.referral_count or 0) + 1
            await session.commit()
        await self.add_trial_days(telegram_id, days=days)

    async def get_premium_status(self, telegram_id: int) -> dict | None:
        """Возвращает информацию о премиуме и остатке времени"""
        user = await self.get_user(telegram_id)
        if not user:
            return None
        now = datetime.utcnow()
        remaining = None
        if user.premium_until:
            delta = user.premium_until - now
            remaining = max(delta, timedelta(0))
        return {
            "is_premium": user.is_premium,
            "premium_plan": user.premium_plan,
            "premium_started_at": user.premium_started_at,
            "premium_until": user.premium_until,
            "remaining": remaining,
        }

    async def set_last_payment_charge_id(self, telegram_id: int, charge_id: str):
        """Сохранить ID последнего платежа"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                user = User(
                    telegram_id=telegram_id,
                    last_payment_charge_id=charge_id,
                    created_at=datetime.utcnow(),
                )
                session.add(user)
            else:
                user.last_payment_charge_id = charge_id
            await session.commit()

    async def revoke_premium(self, telegram_id: int):
        """Снять премиум-доступ"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            user.is_premium = False
            user.premium_plan = None
            user.premium_started_at = None
            user.premium_until = datetime.utcnow()
            await session.commit()

    async def clear_premium_data(self, telegram_id: int):
        """Полностью удалить данные о премиуме пользователя"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return
            user.is_premium = False
            user.premium_plan = None
            user.premium_started_at = None
            user.premium_until = None
            user.last_payment_charge_id = None
            user.gift_from_username = None
            user.trial_review_offered = False
            user.trial_review_used = False
            await session.commit()
    
    async def save_session(self, user_id: int, session_string: str, phone: str = None) -> UserSession:
        """Сохранить сессию пользователя"""
        async with self.async_session() as session:
            # Деактивируем старые сессии
            await session.execute(
                update(UserSession)
                .where(UserSession.user_id == user_id)
                .values(is_active=False)
            )
            
            # Создаем новую активную сессию
            user_session = UserSession(
                user_id=user_id,
                session_string=session_string,
                phone=phone,
                is_active=True,
                last_used=datetime.utcnow()
            )
            session.add(user_session)
            await session.commit()
            await session.refresh(user_session)
            return user_session
    
    async def get_active_session(self, user_id: int) -> UserSession:
        """Получить активную сессию пользователя"""
        async with self.async_session() as session:
            result = await session.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id, UserSession.is_active == True)
            )
            return result.scalar_one_or_none()
    
    async def get_broadcast_settings(self, user_id: int) -> BroadcastSettings:
        """Получить настройки рассылки пользователя"""
        async with self.async_session() as session:
            result = await session.execute(
                select(BroadcastSettings)
                .where(BroadcastSettings.user_id == user_id)
            )
            return result.scalar_one_or_none()
    
    async def create_or_update_broadcast_settings(self, user_id: int, **kwargs) -> BroadcastSettings:
        """Создать или обновить настройки рассылки"""
        async with self.async_session() as session:
            result = await session.execute(
                select(BroadcastSettings)
                .where(BroadcastSettings.user_id == user_id)
            )
            settings = result.scalar_one_or_none()
            
            if settings:
                for key, value in kwargs.items():
                    if hasattr(settings, key):
                        setattr(settings, key, value)
                settings.updated_at = datetime.utcnow()
            else:
                settings = BroadcastSettings(user_id=user_id, **kwargs)
                session.add(settings)
            
            await session.commit()
            await session.refresh(settings)
            return settings
    
    async def get_app_setting(self, key: str) -> str:
        """Получить настройку приложения"""
        async with self.async_session() as session:
            result = await session.execute(
                select(AppSettings).where(AppSettings.key == key)
            )
            setting = result.scalar_one_or_none()
            return setting.value if setting else None
    
    async def set_app_setting(self, key: str, value: str) -> AppSettings:
        """Установить настройку приложения"""
        async with self.async_session() as session:
            result = await session.execute(
                select(AppSettings).where(AppSettings.key == key)
            )
            setting = result.scalar_one_or_none()
            
            if setting:
                setting.value = value
                setting.updated_at = datetime.utcnow()
            else:
                setting = AppSettings(key=key, value=value)
                session.add(setting)
            
            await session.commit()
            await session.refresh(setting)
            return setting
    
    async def is_admin(self, telegram_id: int) -> bool:
        """Проверить, является ли пользователь администратором"""
        user = await self.get_user(telegram_id)
        return user.is_admin if user else False
    
    async def set_admin(self, telegram_id: int, is_admin: bool = True):
        """Установить пользователя как администратора"""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                user = User(telegram_id=telegram_id, is_admin=is_admin)
                session.add(user)
            else:
                user.is_admin = is_admin
            await session.commit()

    async def get_all_user_ids(self) -> list[int]:
        """Получить список telegram_id всех пользователей"""
        async with self.async_session() as session:
            result = await session.execute(select(User.telegram_id))
            return [row[0] for row in result.all()]

    async def get_admin_ids(self) -> list[int]:
        """Получить список telegram_id всех администраторов"""
        async with self.async_session() as session:
            result = await session.execute(select(User.telegram_id).where(User.is_admin == True))
            return [row[0] for row in result.all()]


# Глобальный экземпляр БД
db = Database()

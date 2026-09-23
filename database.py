import logging
from datetime import datetime
from os import getenv

from dotenv import load_dotenv
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, relationship

load_dotenv()

Base = declarative_base()

# Задержка между кругами рассылки по умолчанию (в секундах)
DEFAULT_MIN_DELAY = 10 * 60
DEFAULT_MAX_DELAY = 15 * 60


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=True)
    broadcast_sent_total = Column(Integer, default=0)
    broadcast_active_seconds = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

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

    user = relationship("User", back_populates="sessions")


class BroadcastSettings(Base):
    __tablename__ = "broadcast_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, unique=True)
    min_delay_seconds = Column(Integer, default=DEFAULT_MIN_DELAY)
    max_delay_seconds = Column(Integer, default=DEFAULT_MAX_DELAY)
    text = Column(Text, nullable=True)
    file_id = Column(String(255), nullable=True)
    media_type = Column(String(50), nullable=True)
    caption = Column(Text, nullable=True)
    text_entities = Column(JSON, nullable=True)
    caption_entities = Column(JSON, nullable=True)
    selected_folders = Column(JSON, nullable=True)
    selected_chats = Column(JSON, nullable=True)
    # Рассылка должна идти: после рестарта бота такие рассылки запускаются снова
    is_running = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="broadcast_settings")


# Миграции для баз, созданных старыми версиями бота. Каждая идемпотентна.
MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS broadcast_sent_total INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS broadcast_active_seconds INTEGER DEFAULT 0",
    "ALTER TABLE broadcast_settings ADD COLUMN IF NOT EXISTS text_entities JSON",
    "ALTER TABLE broadcast_settings ADD COLUMN IF NOT EXISTS caption_entities JSON",
    "ALTER TABLE broadcast_settings ADD COLUMN IF NOT EXISTS is_running BOOLEAN DEFAULT FALSE",
    # Задержки раньше хранились в минутах: переводим в секунды (один раз).
    # 0/0 означало рассылку без пауз — такие настройки получают значения по умолчанию.
    f"""
    DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'broadcast_settings' AND column_name = 'min_delay') THEN
            ALTER TABLE broadcast_settings RENAME COLUMN min_delay TO min_delay_seconds;
            ALTER TABLE broadcast_settings RENAME COLUMN max_delay TO max_delay_seconds;
            UPDATE broadcast_settings SET
                min_delay_seconds = CASE WHEN COALESCE(max_delay_seconds, 0) = 0
                                         THEN {DEFAULT_MIN_DELAY} ELSE min_delay_seconds * 60 END,
                max_delay_seconds = CASE WHEN COALESCE(max_delay_seconds, 0) = 0
                                         THEN {DEFAULT_MAX_DELAY} ELSE max_delay_seconds * 60 END;
        END IF;
    END $$
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_broadcast_settings_user_id ON broadcast_settings (user_id)",
]


class Database:
    def __init__(self):
        db_url = getenv("DATABASE_URL")
        if not db_url:
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
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        for statement in MIGRATIONS:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(text(statement))
            except Exception as e:
                logging.warning(f"Migration error ({statement.strip()[:60]}...): {e}")

    async def close(self):
        await self.engine.dispose()

    async def get_user(self, telegram_id: int) -> User:
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            return result.scalar_one_or_none()

    async def get_or_create_user(self, telegram_id: int, username: str = None, full_name: str = None) -> User:
        """Найти пользователя (обновив имя и username) или создать нового"""
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalar_one_or_none()
            if not user:
                user = User(telegram_id=telegram_id)
                session.add(user)
            if username:
                user.username = username.lstrip("@")
            if full_name:
                user.full_name = full_name
            await session.commit()
            return user

    async def increment_broadcast_stats(self, telegram_id: int, sent_inc: int = 0, active_seconds_inc: int = 0):
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalar_one_or_none()
            if not user:
                return
            if sent_inc:
                user.broadcast_sent_total = (user.broadcast_sent_total or 0) + sent_inc
            if active_seconds_inc:
                user.broadcast_active_seconds = (user.broadcast_active_seconds or 0) + active_seconds_inc
            await session.commit()

    async def save_session(self, user_id: int, session_string: str, phone: str = None) -> UserSession:
        async with self.async_session() as session:
            await session.execute(
                update(UserSession)
                .where(UserSession.user_id == user_id)
                .values(is_active=False)
            )

            user_session = UserSession(
                user_id=user_id,
                session_string=session_string,
                phone=phone,
                is_active=True,
                last_used=datetime.utcnow(),
            )
            session.add(user_session)
            await session.commit()
            await session.refresh(user_session)
            return user_session

    async def get_active_session(self, user_id: int) -> UserSession:
        async with self.async_session() as session:
            result = await session.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id, UserSession.is_active == True)
                .order_by(UserSession.id.desc())
            )
            return result.scalars().first()

    async def deactivate_sessions(self, user_id: int):
        async with self.async_session() as session:
            await session.execute(
                update(UserSession)
                .where(UserSession.user_id == user_id)
                .values(is_active=False)
            )
            await session.commit()

    async def get_broadcast_settings(self, user_id: int) -> BroadcastSettings:
        async with self.async_session() as session:
            result = await session.execute(
                select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def create_or_update_broadcast_settings(self, user_id: int, **kwargs) -> BroadcastSettings:
        async with self.async_session() as session:
            result = await session.execute(
                select(BroadcastSettings).where(BroadcastSettings.user_id == user_id)
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

    async def get_running_broadcast_user_ids(self) -> list[int]:
        async with self.async_session() as session:
            result = await session.execute(
                select(BroadcastSettings.user_id).where(BroadcastSettings.is_running == True)
            )
            return list(result.scalars())


db = Database()

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
    func,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, relationship

load_dotenv()

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)
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
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    min_delay = Column(Integer, default=0)
    max_delay = Column(Integer, default=0)
    text = Column(Text, nullable=True)
    file_id = Column(String(255), nullable=True)
    media_type = Column(String(50), nullable=True)
    caption = Column(Text, nullable=True)
    text_entities = Column(JSON, nullable=True)
    caption_entities = Column(JSON, nullable=True)
    selected_folders = Column(JSON, nullable=True)
    selected_chats = Column(JSON, nullable=True)
    messages_per_chat = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="broadcast_settings")


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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

        await self._migrate_add_is_admin_column()
        await self._migrate_add_broadcast_entities()
        await self._migrate_add_broadcast_stats_columns()

    async def _migrate_add_is_admin_column(self):
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='is_admin'
                        """
                    )
                )
                exists = result.scalar_one_or_none() is not None
                if not exists:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE users
                            ADD COLUMN is_admin BOOLEAN DEFAULT FALSE
                            """
                        )
                    )
                    logging.info("Migration: Added is_admin column to users table")
            except Exception as e:
                logging.warning(f"Migration error (is_admin): {e}")

    async def _migrate_add_broadcast_entities(self):
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='broadcast_settings' AND column_name='text_entities'
                        """
                    )
                )
                text_entities_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='broadcast_settings' AND column_name='caption_entities'
                        """
                    )
                )
                caption_entities_exists = result.scalar_one_or_none() is not None

                if not text_entities_exists:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE broadcast_settings
                            ADD COLUMN text_entities JSON
                            """
                        )
                    )
                    logging.info("Migration: Added text_entities column to broadcast_settings table")

                if not caption_entities_exists:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE broadcast_settings
                            ADD COLUMN caption_entities JSON
                            """
                        )
                    )
                    logging.info("Migration: Added caption_entities column to broadcast_settings table")
            except Exception as e:
                logging.warning(f"Migration error (broadcast entities): {e}")

    async def _migrate_add_broadcast_stats_columns(self):
        async with self.engine.begin() as conn:
            try:
                result = await conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='broadcast_sent_total'
                        """
                    )
                )
                sent_exists = result.scalar_one_or_none() is not None

                result = await conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name='users' AND column_name='broadcast_active_seconds'
                        """
                    )
                )
                time_exists = result.scalar_one_or_none() is not None

                if not sent_exists:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE users
                            ADD COLUMN broadcast_sent_total INTEGER DEFAULT 0
                            """
                        )
                    )
                    logging.info("Migration: Added broadcast_sent_total column to users table")

                if not time_exists:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE users
                            ADD COLUMN broadcast_active_seconds INTEGER DEFAULT 0
                            """
                        )
                    )
                    logging.info("Migration: Added broadcast_active_seconds column to users table")
            except Exception as e:
                logging.warning(f"Migration error (broadcast stats): {e}")

    async def close(self):
        await self.engine.dispose()

    async def get_user(self, telegram_id: int) -> User:
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            return result.scalar_one_or_none()

    async def get_user_by_username(self, username: str) -> User:
        if not username:
            return None
        normalized = username.lstrip("@").lower()
        async with self.async_session() as session:
            result = await session.execute(select(User).where(func.lower(User.username) == normalized))
            return result.scalar_one_or_none()

    async def create_user(
        self,
        telegram_id: int,
        username: str = None,
        full_name: str = None,
    ) -> User:
        async with self.async_session() as session:
            user = User(
                telegram_id=telegram_id,
                username=username.lstrip("@") if username else None,
                full_name=full_name,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def get_or_create_user(self, telegram_id: int, username: str = None, full_name: str = None) -> User:
        user = await self.get_user(telegram_id)
        if not user:
            user = await self.create_user(telegram_id, username, full_name)
        return user

    async def update_user_info(self, telegram_id: int, username: str = None, full_name: str = None):
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalar_one_or_none()
            if not user:
                return
            if username:
                user.username = username.lstrip("@")
            if full_name:
                user.full_name = full_name
            await session.commit()

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
                select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
            )
            return result.scalar_one_or_none()

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

    async def get_app_setting(self, key: str) -> str:
        async with self.async_session() as session:
            result = await session.execute(select(AppSettings).where(AppSettings.key == key))
            setting = result.scalar_one_or_none()
            return setting.value if setting else None

    async def set_app_setting(self, key: str, value: str) -> AppSettings:
        async with self.async_session() as session:
            result = await session.execute(select(AppSettings).where(AppSettings.key == key))
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
        user = await self.get_user(telegram_id)
        return user.is_admin if user else False

    async def set_admin(self, telegram_id: int, is_admin: bool = True):
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalar_one_or_none()
            if not user:
                user = User(telegram_id=telegram_id, is_admin=is_admin)
                session.add(user)
            else:
                user.is_admin = is_admin
            await session.commit()

    async def get_all_user_ids(self) -> list[int]:
        async with self.async_session() as session:
            result = await session.execute(select(User.telegram_id))
            return [row[0] for row in result.all()]


db = Database()

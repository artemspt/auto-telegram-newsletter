"""Проверка логики: python test_main.py"""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456789:" + "x" * 35)
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/none")

from telethon.tl.types import (
    DialogFilter,
    DialogFilterChatlist,
    InputPeerChannel,
    InputPeerUser,
    TextWithEntities,
)

from telethon.errors import ChatWriteForbiddenError, PeerFloodError

from main import _folder_dialogs, _format_seconds, _is_account_error, _parse_delay


def dialog(peer_id, kind, archived=False, unread=0, muted=False, bot=False, contact=False):
    mute_until = datetime.now(timezone.utc) + timedelta(days=1) if muted else None
    return SimpleNamespace(
        id=peer_id,
        entity=SimpleNamespace(bot=bot, contact=contact),
        is_user=kind == "user",
        is_group=kind == "group",
        archived=archived,
        unread_count=unread,
        dialog=SimpleNamespace(unread_mark=False, notify_settings=SimpleNamespace(mute_until=mute_until)),
    )


def folder(**kw):
    kw.setdefault("pinned_peers", [])
    kw.setdefault("include_peers", [])
    kw.setdefault("exclude_peers", [])
    return DialogFilter(id=2, title=TextWithEntities("f", []), **kw)


def ids(folder_obj, dialogs):
    return {d.id for d in _folder_dialogs(folder_obj, dialogs)}


friend = dialog(10, "user", contact=True)
stranger = dialog(11, "user")
bot = dialog(12, "user", bot=True)
group = dialog(-1000000000020, "group")
muted_group = dialog(-1000000000021, "group", muted=True)
channel = dialog(-1000000000030, "channel", archived=True)
all_dialogs = [friend, stranger, bot, group, muted_group, channel]

# Закреплённые чаты входят в папку наравне с добавленными
f = folder(pinned_peers=[InputPeerChannel(30, 0)], include_peers=[InputPeerUser(11, 0)])
assert ids(f, all_dialogs) == {channel.id, stranger.id}

# Флаг «группы» + исключение конкретной группы + «без замьюченных»
f = folder(groups=True, exclude_muted=True, exclude_peers=[InputPeerChannel(20, 0)])
assert ids(f, all_dialogs) == set()
assert ids(folder(groups=True, exclude_muted=True), all_dialogs) == {group.id}

# Типы пользователей
assert ids(folder(contacts=True), all_dialogs) == {friend.id}
assert ids(folder(non_contacts=True), all_dialogs) == {stranger.id}
assert ids(folder(bots=True), all_dialogs) == {bot.id}

# Архив исключается, но явно добавленный чат остаётся
assert ids(folder(broadcasts=True, exclude_archived=True), all_dialogs) == set()
f = folder(broadcasts=True, exclude_archived=True, include_peers=[InputPeerChannel(30, 0)])
assert ids(f, all_dialogs) == {channel.id}

# «Только непрочитанные»
unread_friend = dialog(13, "user", contact=True, unread=2)
assert ids(folder(contacts=True, exclude_read=True), [friend, unread_friend]) == {unread_friend.id}

# Папка-ссылка: только явные чаты
chatlist = DialogFilterChatlist(
    id=3, title=TextWithEntities("c", []), pinned_peers=[], include_peers=[InputPeerChannel(20, 0)]
)
assert ids(chatlist, all_dialogs) == {group.id}

# Задержки: число — минуты, «с» — секунды
assert _parse_delay("15") == 900
assert _parse_delay("15 мин") == _parse_delay("15m") == 900
assert _parse_delay("30с") == _parse_delay("30 сек") == _parse_delay("30s") == 30
assert _parse_delay("abc") is None and _parse_delay("") is None and _parse_delay("-5") is None
assert [_format_seconds(x) for x in (10, 60, 90, 3600)] == ["10с", "1м", "1м 30с", "1ч 0м"]

# Остановка рассылки: спам-ограничение и непонятные сбои — проблема аккаунта, запрет в чате — нет
assert _is_account_error(PeerFloodError(None)) and _is_account_error(ConnectionError())
assert not _is_account_error(ChatWriteForbiddenError(None))

print("ok")

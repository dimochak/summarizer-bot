"""Правила реакцій-картинок: тригер -> набір картинок -> кулдаун.

Два види тригерів:
- тема в тексті (RULES): регулярка + LLM-підтвердження, як для Кличка;
- тег конкретної людини (MENTION_RULES): з секрету MENTION_REACTIONS, бо
  юзернеймам та ID людей не місце в публічному репозиторії.

Картинки лежать у `<REACTIONS_DIR>/<name>/` і в git не потрапляють.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import src.tools.config as config

# Картинки не зберігаються в репозиторії — шлях задає REACTIONS_DIR.
ASSETS_DIR = Path(config.REACTIONS_DIR)


@dataclass(frozen=True)
class ReactionRule:
    # Імʼя правила: і ключ кулдауну в БД, і назва теки з картинками.
    name: str
    cooldown_seconds: int
    # Дешевий префільтр на кожне повідомлення. Може давати хибні збіги —
    # їх відсіює LLM-підтвердження, яке кличеться лише на збігах.
    pattern: re.Pattern | None = None
    # Про кого/що йдеться — для LLM-підтвердження й підпису.
    subject: str = ""
    # Особливості, які LLM має врахувати, щоб не спрацювати хибно.
    exclusions: str = ""
    # Чи писати до картинки підпис від LLM. Без підпису реакція швидша й
    # дешевша: лишається тільки підтвердження теми.
    generate_caption: bool = False
    # Тег людини: Telegram сам каже, кого тегнули, тож модель не потрібна.
    # ID — для тегу людини без юзернейма (вибір зі списку учасників),
    # юзернейм — для звичайного @username.
    mention_user_ids: frozenset[int] = frozenset()
    mention_usernames: frozenset[str] = frozenset()

    @property
    def is_mention(self) -> bool:
        return bool(self.mention_user_ids or self.mention_usernames)

    @property
    def needs_confirmation(self) -> bool:
        return not self.is_mention

    @property
    def images_dir(self) -> Path:
        return ASSETS_DIR / self.name


RULES: list[ReactionRule] = [
    ReactionRule(
        name="klitschko",
        # Основа прізвища ловить усі відмінки (Кличко, Кличка, Кличку, Кличком)
        # і латинське написання.
        pattern=re.compile(r"кличк|klitschk|klychk", re.IGNORECASE),
        subject="Віталій Кличко — мер Києва, колишній боксер",
        # «Кличка» — ще й звичайне слово: прізвисько тварини чи людини.
        # Його відмінки збігаються з відмінками прізвища, тож регулярка
        # спрацює на «кличку собаки» — це має відсіяти підтвердження.
        exclusions=(
            "Слово «кличка» у значенні прізвиська тварини чи людини "
            "(«кличка собаки», «яка в нього кличка») — НЕ рахується."
        ),
        cooldown_seconds=3600,
        # Картинка говорить сама за себе.
        generate_caption=False,
    ),
    ReactionRule(
        name="trump",
        # «Трамплін» починається так само, тож відсікаємо його вже регуляркою,
        # не витрачаючи виклик моделі. Латиницею — ціле слово: інакше
        # спрацювало б на «trumpet».
        pattern=re.compile(r"трамп(?!л[іи]н)|\btrump\b", re.IGNORECASE),
        subject="Дональд Трамп — президент США",
        exclusions=(
            "Англійське «trump» у загальному значенні («trump card» — козир) "
            "— НЕ рахується."
        ),
        cooldown_seconds=3600,
        generate_caption=False,
    ),
]


MENTION_COOLDOWN_SECONDS = 3600
_FOLDER = re.compile(r"^[a-z0-9_-]+$")


def parse_mention_reactions(raw: str | None) -> list[ReactionRule]:
    """`@username=folder,123456789=folder` -> правила за тегом.

    Кілька ключів з однією текою складаються в одне правило: так людину
    можна вказати і за ID, і за юзернеймом, а кулдаун у неї буде спільний.
    Хибні записи пропускаємо з попередженням, а не валимо старт бота.
    """
    ids: dict[str, set[int]] = {}
    usernames: dict[str, set[str]] = {}
    for n, item in enumerate((raw or "").split(","), 1):
        item = item.strip()
        if not item:
            continue
        key, sep, folder = (part.strip() for part in item.partition("="))
        folder = folder.lower()
        if not sep or not key or not _FOLDER.match(folder):
            # Сам запис не друкуємо: там юзернейм чи ID людини.
            config.log.warning(f"MENTION_REACTIONS: пропускаю хибний запис №{n}")
            continue
        ids.setdefault(folder, set())
        usernames.setdefault(folder, set())
        if key.lstrip("-").isdigit():
            ids[folder].add(int(key))
        else:
            usernames[folder].add(key.lstrip("@").lower())
    return [
        ReactionRule(
            name=folder,
            cooldown_seconds=MENTION_COOLDOWN_SECONDS,
            mention_user_ids=frozenset(ids[folder]),
            mention_usernames=frozenset(usernames[folder]),
        )
        for folder in ids
    ]


MENTION_RULES: list[ReactionRule] = parse_mention_reactions(os.getenv("MENTION_REACTIONS"))


def all_rules() -> list[ReactionRule]:
    return RULES + MENTION_RULES

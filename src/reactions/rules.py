"""Правила реакцій-картинок: тема -> набір картинок -> кулдаун.

Нове правило — це один запис у RULES і тека з картинками
`<REACTIONS_DIR>/<name>/`. Самі картинки в git не потрапляють.
"""
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
    # Дешевий префільтр на кожне повідомлення. Може давати хибні збіги —
    # їх відсіює LLM-підтвердження, яке кличеться лише на збігах.
    pattern: re.Pattern
    # Про кого/що йдеться — для LLM-підтвердження й підпису.
    subject: str
    # Особливості, які LLM має врахувати, щоб не спрацювати хибно.
    exclusions: str
    cooldown_seconds: int
    # Чи писати до картинки підпис від LLM. Без підпису реакція швидша й
    # дешевша: лишається тільки підтвердження теми.
    generate_caption: bool = False

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

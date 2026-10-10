"""Правила реакцій-картинок: тема -> набір картинок -> кулдаун.

Нове правило — це один запис у RULES і тека з картинками
`assets/reactions/<name>/`.
"""
import re
from dataclasses import dataclass
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets" / "reactions"


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
        cooldown_seconds=3 * 3600,
    ),
]

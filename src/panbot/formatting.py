"""Приведення відповіді LLM до HTML, який приймає Telegram.

Раніше тут була одна регулярка на `**bold**`. Усе інше йшло в Telegram як є,
тож будь-який `<div>`, голий `&` чи незакритий `<b>` від моделі призводили до
помилки парсингу — і користувач не бачив ВЗАГАЛІ нічого.

Стратегія: екранувати все, потім вибірково повернути дозволені теги.
Що не пройшло — лишається видимим текстом, а не ламає повідомлення.
"""
import html
import re

# Теги, які Telegram приймає у parse_mode=HTML.
ALLOWED_TAGS = frozenset({
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "span", "tg-spoiler", "tg-emoji", "a", "code", "pre", "blockquote",
})

_SAFE_URL = re.compile(r"^(?:https?://|tg://)[^\s\"'<>]+$", re.IGNORECASE)

# Теги, які модель вивела літерально, — після екранування вони виглядають так.
_ESCAPED_TAG = re.compile(r"&lt;(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s[^&]*?)?)\s*/?&gt;")
_HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+))""", re.IGNORECASE)
_REAL_TAG = re.compile(r"<(/?)([a-z][a-z0-9-]*)((?:\s[^>]*)?)>", re.IGNORECASE)

_FENCED_CODE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")

_SENTINEL = "\x00codeblock:{}\x00"


def _restore_allowed_tags(escaped: str) -> str:
    """Повертає до життя лише теги з ALLOWED_TAGS; решта лишається текстом."""

    def repl(m: re.Match) -> str:
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3) or ""
        if tag not in ALLOWED_TAGS:
            return m.group(0)  # лишаємо екранованим — просто видимий текст
        if closing:
            return f"</{tag}>"

        # Атрибути дозволяємо точково: усе інше відкидаємо.
        if tag == "a":
            href_match = _HREF.search(html.unescape(attrs))
            if not href_match:
                return "<a>"
            url = next(g for g in href_match.groups() if g is not None)
            if not _SAFE_URL.match(url):
                return "<a>"
            return f'<a href="{html.escape(url, quote=True)}">'
        if tag == "span":
            # Telegram знає єдиний варіант span — спойлер.
            if "tg-spoiler" in attrs:
                return '<span class="tg-spoiler">'
            return "<span>"
        return f"<{tag}>"

    return _ESCAPED_TAG.sub(repl, escaped)


def _balance_tags(text: str) -> str:
    """Закриває незакриті теги й викидає зайві закривні.

    Telegram відхиляє повідомлення з незбалансованою розміткою, а LLM регулярно
    обриває тег на межі ліміту токенів.
    """
    out: list[str] = []
    stack: list[str] = []
    last = 0

    for m in _REAL_TAG.finditer(text):
        out.append(text[last:m.start()])
        last = m.end()
        closing, tag = m.group(1), m.group(2).lower()

        if not closing:
            stack.append(tag)
            out.append(m.group(0))
            continue

        if tag not in stack:
            continue  # закриття без відкриття — просто викидаємо

        while stack and stack[-1] != tag:
            out.append(f"</{stack.pop()}>")
        stack.pop()
        out.append(f"</{tag}>")

    out.append(text[last:])
    out.extend(f"</{tag}>" for tag in reversed(stack))
    return "".join(out)


def format_telegram_html(text: str) -> str:
    if not text:
        return ""

    # 1. Код виймаємо ПЕРШИМ, щоб розмітка всередині нього лишилась як є.
    blocks: list[str] = []

    def stash(rendered: str) -> str:
        blocks.append(rendered)
        return _SENTINEL.format(len(blocks) - 1)

    def fenced(m: re.Match) -> str:
        lang, body = m.group(1), m.group(2)
        body = html.escape(body.strip("\n"), quote=False)
        if lang:
            return stash(f'<pre><code class="language-{html.escape(lang, quote=True)}">{body}</code></pre>')
        return stash(f"<pre>{body}</pre>")

    text = _FENCED_CODE.sub(fenced, text)
    text = _INLINE_CODE.sub(
        lambda m: stash(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text
    )

    # 2. Екрануємо все інше.
    text = html.escape(text, quote=False)

    # 3. Повертаємо дозволені теги, які модель вивела як HTML.
    text = _restore_allowed_tags(text)

    # 4. Markdown, який модель вивела попри інструкції.
    text = _MD_LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>'
        if _SAFE_URL.match(html.unescape(m.group(2)))
        else m.group(0),
        text,
    )
    text = _BOLD.sub(r"<b>\1</b>", text)

    # 5. Лагодимо незбалансовані теги й повертаємо код на місце.
    text = _balance_tags(text)
    for i, block in enumerate(blocks):
        text = text.replace(_SENTINEL.format(i), block)

    return text

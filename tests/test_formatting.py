"""Тести санітайзера HTML для Telegram.

Головна вимога: що б не повернула модель, повідомлення має лишитись
відправлюваним. Втрата форматування прийнятна, помилка парсингу — ні.
"""
import pytest

from src.panbot.formatting import format_telegram_html as fmt


def test_empty_input():
    assert fmt("") == ""
    assert fmt(None) == ""


def test_plain_text_untouched():
    assert fmt("просто текст") == "просто текст"


def test_markdown_bold_becomes_tag():
    assert fmt("це **важливо**") == "це <b>важливо</b>"


def test_bare_ampersand_and_angle_brackets_are_escaped():
    # Раніше саме це валило відправку з parse error.
    assert fmt("R&D та 5 < 10") == "R&amp;D та 5 &lt; 10"


def test_allowed_tags_survive():
    assert fmt("<b>жирний</b> та <i>курсив</i>") == "<b>жирний</b> та <i>курсив</i>"


def test_disallowed_tag_becomes_visible_text():
    out = fmt("<div>щось</div>")
    assert "<div>" not in out
    assert "&lt;div&gt;" in out


def test_script_tag_is_neutralised():
    out = fmt("<script>alert(1)</script>")
    assert "<script>" not in out


def test_unclosed_tag_is_closed():
    # LLM обриває тег, коли впирається в ліміт токенів.
    assert fmt("<b>обірвано") == "<b>обірвано</b>"


def test_stray_closing_tag_is_dropped():
    assert fmt("текст</b>") == "текст"


def test_mismatched_nesting_is_repaired():
    out = fmt("<b><i>текст</b></i>")
    assert out == "<b><i>текст</i></b>"


def test_link_href_preserved():
    out = fmt('<a href="https://example.com">тиць</a>')
    assert out == '<a href="https://example.com">тиць</a>'


def test_javascript_url_is_stripped():
    out = fmt('<a href="javascript:alert(1)">тиць</a>')
    assert "javascript:" not in out


def test_markdown_link_converted():
    out = fmt("[документація](https://example.com/docs)")
    assert out == '<a href="https://example.com/docs">документація</a>'


def test_markdown_link_with_unsafe_scheme_not_converted():
    out = fmt("[тиць](javascript:alert(1))")
    assert "<a" not in out


def test_inline_code_escapes_content():
    out = fmt("виклич `foo<bar> && baz`")
    assert out == "виклич <code>foo&lt;bar&gt; &amp;&amp; baz</code>"


def test_fenced_code_block_with_language():
    out = fmt("ось код:\n```python\nif a < b:\n    pass\n```")
    assert '<pre><code class="language-python">' in out
    assert "a &lt; b" in out


def test_markdown_inside_code_is_not_converted():
    out = fmt("`**не жирний**`")
    assert "<b>" not in out
    assert "**не жирний**" in out


def test_spoiler_span_preserved():
    out = fmt('<span class="tg-spoiler">секрет</span>')
    assert out == '<span class="tg-spoiler">секрет</span>'


@pytest.mark.parametrize(
    "raw",
    [
        "<b>a<i>b</b>c</i>d",
        "<<>>&&",
        "**незакритий жирний",
        "<a href='https://x.com'>x</a> & <b>y",
        "```\nбез мови\n```",
        "<blockquote>цитата</blockquote>",
    ],
)
def test_output_is_always_balanced(raw):
    """Кожен реальний тег у результаті має мати пару."""
    import re

    out = fmt(raw)
    stack = []
    for m in re.finditer(r"<(/?)([a-z][a-z0-9-]*)(?:\s[^>]*)?>", out):
        if m.group(1):
            assert stack and stack[-1] == m.group(2), f"незбалансовано: {out!r}"
            stack.pop()
        else:
            stack.append(m.group(2))
    assert not stack, f"лишились незакриті теги: {out!r}"

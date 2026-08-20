"""Тести інструмента читання веб-сторінок.

Найважливіше тут — не форматування, а те, що агент не постукає у внутрішню
мережу: URL приходить від моделі, а та могла прочитати його зі сторінки або
з повідомлення користувача.
"""
import httpx
import pytest

from src.agent.tools.web import _html_to_text, _is_public_url, web_fetch


class FakeResponse:
    def __init__(self, text="", content_type="text/html; charset=utf-8", size=100):
        self.text = text
        self.content = b"x" * size
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False

    async def get(self, url, headers=None):
        self.calls.append(url)
        return self._response


@pytest.fixture
def fake_http(monkeypatch):
    def install(response):
        client = FakeClient(response)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
        return client
    return install


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8080/",
        "http://169.254.169.254/latest/meta-data/",   # метадані хостингу
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
    ],
)
def test_internal_addresses_are_rejected(url):
    allowed, reason = _is_public_url(url)
    assert not allowed, f"{url} мав бути відхилений"
    assert reason


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)"])
def test_non_http_schemes_are_rejected(url):
    allowed, _ = _is_public_url(url)
    assert not allowed


def test_public_url_is_allowed():
    allowed, _ = _is_public_url("https://example.com/page")
    assert allowed


async def test_internal_url_is_not_even_fetched(monkeypatch):
    """Перевірка має спрацювати ДО мережевого запиту."""
    def explode(**kw):
        raise AssertionError("не мало доходити до HTTP-запиту")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    result = await web_fetch.ainvoke({"url": "http://169.254.169.254/"})
    assert "Не можу відкрити" in result


async def test_html_is_reduced_to_text(fake_http):
    html = """<html><head><style>body{color:red}</style></head>
    <body><script>alert(1)</script><h1>Заголовок</h1>
    <p>Текст &amp; ще текст</p></body></html>"""
    fake_http(FakeResponse(text=html))

    result = await web_fetch.ainvoke({"url": "https://example.com"})

    assert "Заголовок" in result
    assert "Текст & ще текст" in result
    assert "alert(1)" not in result, "вміст script потрапив у текст"
    assert "color:red" not in result, "вміст style потрапив у текст"
    assert "<h1>" not in result


async def test_non_text_content_is_refused(fake_http):
    fake_http(FakeResponse(content_type="application/pdf"))
    result = await web_fetch.ainvoke({"url": "https://example.com/doc.pdf"})
    assert "не текстова" in result


async def test_huge_page_is_refused(fake_http):
    fake_http(FakeResponse(text="x", size=5_000_000))
    result = await web_fetch.ainvoke({"url": "https://example.com/big"})
    assert "завелика" in result


async def test_long_text_is_truncated(fake_http):
    fake_http(FakeResponse(text="<p>" + ("слово " * 20000) + "</p>"))
    result = await web_fetch.ainvoke({"url": "https://example.com/long"})
    assert "обрізано" in result
    assert len(result) < 12_000


async def test_network_failure_is_not_fatal(monkeypatch):
    class Broken:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, *a, **kw): raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: Broken())
    result = await web_fetch.ainvoke({"url": "https://example.com"})
    assert "Не вдалося відкрити" in result


def test_entities_and_whitespace_are_normalised():
    text = _html_to_text("<p>a&nbsp;&nbsp;b</p>\n\n\n\n<p>c&mdash;d</p>")
    assert "a  b" in text or "a b" in text
    assert "c—d" in text
    assert "\n\n\n" not in text

"""Загрузка HTML-виджетов с лёгкой минификацией и проверкой лимитов Bot API 10.4."""
import zlib
from functools import cache
from pathlib import Path

DIR = Path(__file__).parent
MAX_RAW = 32768
MAX_ZIP = 4096


def minify(html: str) -> str:
    return "\n".join(line.strip() for line in html.splitlines() if line.strip())


@cache
def load(name: str) -> str:
    html = minify((DIR / f"{name}.html").read_text("utf-8"))
    raw = html.encode()
    zipped = len(zlib.compress(raw, 9))
    if len(raw) > MAX_RAW or zipped > MAX_ZIP:
        raise ValueError(f"widget {name} too large: {len(raw)} bytes, {zipped} compressed")
    return html


def sizes() -> dict[str, tuple[int, int]]:
    out = {}
    for p in sorted(DIR.glob("*.html")):
        raw = minify(p.read_text("utf-8")).encode()
        out[p.stem] = (len(raw), len(zlib.compress(raw, 9)))
    return out

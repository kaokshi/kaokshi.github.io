"""Render an optional *verified* Zen-channel link without changing canonical prose.

Never guess channel addresses or link to the public RSS bridge instead of Dzen.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = {
    "science": "За гранью очевидного",
    "legends": "Легенды тёмных улиц",
    "cinema": "Кадр за Кадром",
}


def channel_from_folder(folder):
    label = Path(folder).name.split("-", 1)[0]
    return label if label in CHANNELS else None


def approved_url(channel, config_path=None):
    if channel not in CHANNELS:
        return None
    path = config_path or ROOT / "config" / "channel_subscription_urls.json"
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    value = settings.get(channel)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_channel_subscription_url")
    url = urlsplit(value)
    if (url.scheme != "https" or url.netloc not in {"dzen.ru", "www.dzen.ru"}
            or not url.path or url.username or url.password or url.port
            or url.query or url.fragment or any(ord(c) < 32 for c in value)):
        raise ValueError("invalid_channel_subscription_url")
    return value


def split_cta(value, channel, url):
    """Return prose fragments for a named link; do not invent text or URL."""
    name = CHANNELS.get(channel)
    if not url or not name:
        return None
    # Link the channel's name only, preserving punctuation and quotation marks.
    location = value.rfind(name)
    if location < 0:
        return None
    return value[:location], name, value[location + len(name):]

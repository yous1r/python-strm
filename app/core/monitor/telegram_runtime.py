import os
import re
import urllib.parse
from typing import Iterable

from telethon import TelegramClient


_VALID_PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")
_RESERVED_TELEGRAM_PATHS = {"c", "joinchat", "setlanguage"}


def parse_telegram_proxy(proxy: str) -> dict | None:
    """将配置中的代理字符串转换为 Telethon 可识别的参数。"""
    if not proxy or not proxy.strip():
        return None

    proxy_str = proxy.strip()
    if not proxy_str.startswith(_VALID_PROXY_SCHEMES):
        proxy_str = f"http://{proxy_str}"

    parsed = urllib.parse.urlparse(proxy_str)
    if not parsed.hostname or not parsed.port:
        raise ValueError("代理地址格式错误，缺少主机或端口")

    proxy_type = parsed.scheme.lower()
    if proxy_type in {"http", "https"}:
        proxy_type = "http"
    elif proxy_type in {"socks5", "socks5h"}:
        proxy_type = "socks5"

    return {
        "proxy_type": proxy_type,
        "addr": parsed.hostname,
        "port": parsed.port,
    }


def build_telegram_client(
    api_id: str,
    api_hash: str,
    proxy: str = "",
    session_path: str = "data/session_strm",
) -> TelegramClient:
    """构造 TelegramClient，并统一处理代理与 session 目录。"""
    client_kwargs = {}
    proxy_config = parse_telegram_proxy(proxy)
    if proxy_config:
        client_kwargs["proxy"] = proxy_config

    session_dir = os.path.dirname(session_path)
    if session_dir:
        os.makedirs(session_dir, exist_ok=True)

    return TelegramClient(session_path, api_id, api_hash, **client_kwargs)


def parse_channel_reference(channel: str) -> int | str:
    """统一解析 Telegram 频道标识。"""
    normalized = channel.strip()

    match_c = re.search(r"t\.me/c/(\d+)", normalized)
    if match_c:
        return int(f"-100{match_c.group(1)}")

    match_u = re.search(r"t\.me/([a-zA-Z0-9_]+)", normalized)
    if match_u and match_u.group(1) not in _RESERVED_TELEGRAM_PATHS:
        return match_u.group(1)

    if normalized.startswith("@"):
        return normalized[1:]

    try:
        return int(normalized)
    except ValueError:
        return normalized


def parse_channels(channels: Iterable[str] | None) -> list[int | str]:
    """批量解析频道，并过滤空值。"""
    parsed_channels: list[int | str] = []
    for channel in channels or []:
        if channel and channel.strip():
            parsed_channels.append(parse_channel_reference(channel))
    return parsed_channels
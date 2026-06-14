import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from typing import Iterable

from loguru import logger
from telethon import TelegramClient


telegram_session_operation_lock = asyncio.Lock()
TELEGRAM_RUNTIME_SESSION_DIR = os.environ.get(
    "PYTHON_STRM_TELEGRAM_RUNTIME_SESSION_DIR",
    os.path.join(tempfile.gettempdir(), "python-strm-telegram-sessions"),
)

_VALID_PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")
_RESERVED_TELEGRAM_PATHS = {"c", "joinchat", "setlanguage"}
_SUPPORTED_SHARE_LINK_PATTERNS = (
    re.compile(r"https?://115(?:cdn)?\.com/s/\w+(?:\?[^\s\"'<>]+)?"),
    re.compile(r"https?://(?:www\.)?123pan\.com/s/\w+-\w+\.html(?:\?[^\s\"'<>]+)?"),
)
_GENERIC_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_MAGNET_PATTERN = re.compile(r"magnet:\?[^\s\"'<>]+")
_TORRENT_NAME_PATTERN = re.compile(r"[^\s\"'<>]+\.torrent(?:\b|$)", re.IGNORECASE)


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

    runtime_session_path = prepare_runtime_session_path(session_path)

    return TelegramClient(runtime_session_path, api_id, api_hash, **client_kwargs)


def prepare_runtime_session_path(session_path: str = "data/session_strm") -> str:
    """Return an isolated runtime session path seeded from the login session."""
    canonical_session_file = _session_file_path(session_path)
    canonical_dir = canonical_session_file.parent
    if canonical_dir:
        canonical_dir.mkdir(parents=True, exist_ok=True)

    runtime_dir = Path(TELEGRAM_RUNTIME_SESSION_DIR)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_base = _runtime_session_base(canonical_session_file, runtime_dir)
    runtime_session_file = _session_file_path(str(runtime_base))

    _remove_runtime_session_sidecars(runtime_session_file)
    if canonical_session_file.exists():
        shutil.copy2(canonical_session_file, runtime_session_file)
        _chmod_owner_only(runtime_session_file)
        _copy_session_sidecars(canonical_session_file, runtime_session_file)

    return str(runtime_base)


def _session_file_path(session_path: str) -> Path:
    path = Path(session_path)
    if str(path).endswith(".session"):
        return path
    return Path(f"{path}.session")


def _runtime_session_base(canonical_session_file: Path, runtime_dir: Path) -> Path:
    digest = hashlib.sha1(str(canonical_session_file.resolve()).encode("utf-8")).hexdigest()[:12]
    return runtime_dir / f"{canonical_session_file.stem}-{os.getpid()}-{digest}"


def _remove_runtime_session_sidecars(runtime_session_file: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = Path(f"{runtime_session_file}{suffix}")
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue


def _copy_session_sidecars(canonical_session_file: Path, runtime_session_file: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        source = Path(f"{canonical_session_file}{suffix}")
        if not source.exists():
            continue

        target = Path(f"{runtime_session_file}{suffix}")
        shutil.copy2(source, target)
        _chmod_owner_only(target)
        if suffix == "-journal":
            logger.warning(
                "Telegram canonical session has a journal file; "
                f"using isolated runtime copy at {runtime_session_file}"
            )


def _chmod_owner_only(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as exc:
        logger.warning(f"Failed to restrict Telegram runtime session permissions for {path}: {exc}")


def close_telegram_session_connection_without_commit(client, *, context: str) -> bool:
    """Close Telethon's SQLite connection after a failed disconnect path."""
    session = getattr(client, "session", None)
    connection = getattr(session, "_conn", None)
    if connection is None:
        return False

    # Telethon's SQLiteSession.close() commits first, which can hit the same lock.
    try:
        connection.close()
        return True
    except Exception as exc:
        logger.warning(f"Failed to force close Telegram session connection after {context}: {exc}")
        return False
    finally:
        try:
            if getattr(session, "_conn", None) is connection:
                session._conn = None
        except Exception as exc:
            logger.warning(f"Failed to detach Telegram session connection after {context}: {exc}")


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


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = (value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _extract_entity_urls(message) -> list[str]:
    urls: list[str] = []

    for entity in getattr(message, "entities", None) or []:
        url = getattr(entity, "url", "") or getattr(entity, "href", "")
        if url:
            urls.append(url)

    buttons = getattr(message, "buttons", None) or []
    for row in buttons:
        button_items = row if isinstance(row, (list, tuple)) else [row]
        for button in button_items:
            url = getattr(button, "url", "")
            if url:
                urls.append(url)

    webpage = getattr(getattr(message, "media", None), "webpage", None)
    webpage_url = getattr(webpage, "url", "")
    if webpage_url:
        urls.append(webpage_url)

    return _dedupe_preserve_order(urls)


def extract_message_torrent_files(message) -> list[dict]:
    """提取消息中携带的种子附件元信息。"""
    if message is None:
        return []

    candidates: list[dict] = []
    file_obj = getattr(message, "file", None)
    document = getattr(message, "document", None)

    file_name = getattr(file_obj, "name", "") if file_obj else ""
    mime_type = getattr(file_obj, "mime_type", "") if file_obj else ""
    size = getattr(file_obj, "size", None) if file_obj else None

    if not file_name and document is not None:
        for attr in getattr(document, "attributes", None) or []:
            attr_name = getattr(attr, "file_name", "")
            if attr_name:
                file_name = attr_name
                break
        if size is None:
            size = getattr(document, "size", None)
        if not mime_type:
            mime_type = getattr(document, "mime_type", "")

    normalized_name = (file_name or "").strip()
    normalized_mime = (mime_type or "").strip().lower()
    is_torrent = normalized_name.lower().endswith(".torrent") or normalized_mime in {
        "application/x-bittorrent",
        "application/octet-stream+torrent",
    }

    if is_torrent:
        candidates.append(
            {
                "name": normalized_name or "unknown.torrent",
                "mime_type": normalized_mime,
                "size": size,
            }
        )

    unique_files: list[dict] = []
    seen: set[tuple[str, str, int | None]] = set()
    for item in candidates:
        key = (item.get("name", ""), item.get("mime_type", ""), item.get("size"))
        if key not in seen:
            seen.add(key)
            unique_files.append(item)
    return unique_files


def _contains_supported_resources(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SUPPORTED_SHARE_LINK_PATTERNS) or bool(
        _MAGNET_PATTERN.search(text) or _TORRENT_NAME_PATTERN.search(text)
    )


def extract_message_text(message) -> str:
    """统一提取 Telethon message 上的文本或 caption。"""
    candidates = {attr: getattr(message, attr, "") for attr in ("text", "raw_text", "message")}
    entity_urls = _extract_entity_urls(message)
    torrent_files = extract_message_torrent_files(message)
    torrent_names = [item["name"] for item in torrent_files if item.get("name")]

    prioritized_chunks: list[str] = []
    fallback_chunks: list[str] = []
    for attr in ("text", "raw_text", "message"):
        value = (candidates.get(attr) or "").strip()
        if not value:
            continue
        if _contains_supported_resources(value):
            prioritized_chunks.append(value)
        else:
            fallback_chunks.append(value)

    merged_chunks = _dedupe_preserve_order([*prioritized_chunks, *fallback_chunks, *entity_urls, *torrent_names])
    if merged_chunks:
        return "\n".join(merged_chunks)

    for attr in ("message", "text", "raw_text"):
        value = (candidates.get(attr) or "").strip()
        if value:
            return value

    return ""

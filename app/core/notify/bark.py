import httpx
import json
import base64
from loguru import logger
from app.config import get_config

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

class BarkNotifier:
    def __init__(self):
        # We fetch config at call time to support hot-reload properly
        pass

    def _build_payload(
        self,
        *,
        title: str,
        content: str,
        message_type: str = "info",
        subtitle: str = "",
        group: str = "",
        level: str = "",
        sound: str = "",
        url: str = "",
        icon: str = "",
        badge: int | None = None,
        call: bool = False,
        auto_copy: bool = False,
        copy: str = "",
        is_archive: bool | None = None,
        action: str = "",
    ) -> dict:
        payload = {
            "title": title,
            "body": content,
        }

        type_defaults = {
            "success": {"level": "active", "sound": "minuet"},
            "error": {"level": "timeSensitive", "sound": "alarm"},
            "critical": {"level": "critical", "sound": "alarm"},
            "warning": {"level": "active", "sound": "bell"},
            "progress": {"level": "passive"},
            "info": {"level": "passive"},
        }
        defaults = type_defaults.get(message_type, type_defaults["info"])

        if subtitle:
            payload["subtitle"] = subtitle
        if group:
            payload["group"] = group

        resolved_level = level or defaults.get("level")
        if resolved_level:
            payload["level"] = resolved_level

        resolved_sound = sound or defaults.get("sound")
        if resolved_sound:
            payload["sound"] = resolved_sound

        if url:
            payload["url"] = url
        if icon:
            payload["icon"] = icon
        if badge is not None:
            payload["badge"] = badge
        if call:
            payload["call"] = "1"
        if auto_copy:
            payload["autoCopy"] = "1"
        if copy:
            payload["copy"] = copy
        if is_archive:
            payload["isArchive"] = "1"
        if action:
            payload["action"] = action

        return payload

    def _encrypt_payload(self, payload: dict, key: str, iv: str, algorithm: str) -> dict:
        """对 Bark 的 payload 进行 AES 加密"""
        data = json.dumps(payload).encode('utf-8')
        key_bytes = key.encode('utf-8')
        iv_bytes = iv.encode('utf-8') if iv else b''
        algo_upper = algorithm.upper()
        
        # GCM 模式处理 (不需要 padding，自带认证标签)
        if "GCM" in algo_upper:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            aesgcm = AESGCM(key_bytes)
            # encrypt 方法自动把 16-byte 的 tag 附在密文末尾
            ciphertext = aesgcm.encrypt(iv_bytes, data, None)
            return {
                "ciphertext": base64.b64encode(ciphertext).decode('utf-8'),
                "iv": iv
            }
        
        # CBC / ECB 模式处理
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded_data = padder.update(data) + padder.finalize()
        
        is_ecb = "ECB" in algo_upper
        mode = modes.ECB() if is_ecb else modes.CBC(iv_bytes)
        
        cipher = Cipher(algorithms.AES(key_bytes), mode, backend=default_backend())
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()
        
        enc_payload = {
            "ciphertext": base64.b64encode(ciphertext).decode('utf-8')
        }
        if not is_ecb and iv:
            enc_payload["iv"] = iv
            
        return enc_payload

    async def send_message(
        self,
        content: str,
        title: str = "系统通知",
        *,
        message_type: str = "info",
        subtitle: str = "",
        group: str = "",
        level: str = "",
        sound: str = "",
        url: str = "",
        icon: str = "",
        badge: int | None = None,
        call: bool = False,
        auto_copy: bool = False,
        copy: str = "",
        is_archive: bool | None = None,
        action: str = "",
    ):
        config = get_config().notify.bark
        if not config.enabled:
            return False
        if not config.server or (not config.device_key and not config.device_keys):
            logger.error("Bark notify is enabled but server or device_key(s) is missing.")
            return False

        server = config.server.rstrip('/')
        push_url = f"{server}/push"
        payload = self._build_payload(
            title=title,
            content=content,
            message_type=message_type,
            subtitle=subtitle,
            group=group,
            level=level,
            sound=sound,
            url=url,
            icon=icon,
            badge=badge,
            call=call,
            auto_copy=auto_copy,
            copy=copy,
            is_archive=is_archive,
            action=action,
        )
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # 判断是否启用了加密
                if config.encryption_key:
                    post_data = self._encrypt_payload(
                        payload, 
                        config.encryption_key, 
                        config.encryption_iv, 
                        config.encryption_algorithm
                    )
                    if config.device_key:
                        post_data["device_key"] = config.device_key
                    if config.device_keys:
                        post_data["device_keys"] = config.device_keys
                else:
                    # 明文 POST 推送 (更规范的方式)
                    post_data = payload.copy()
                    if config.device_key:
                        post_data["device_key"] = config.device_key
                    if config.device_keys:
                        post_data["device_keys"] = config.device_keys
                    
                res = await client.post(push_url, json=post_data)
                data = res.json()
                if data.get("code") != 200:
                    logger.error(f"Bark notify failed: {data.get('message')}")
                    return False
                return True
        except httpx.TimeoutException as e:
            logger.warning(f"Bark notify timeout: server={server}, error={e.__class__.__name__}")
            return False
        except httpx.HTTPError as e:
            logger.error(f"Bark notify request failed: server={server}, error={e.__class__.__name__}: {e}")
            return False
        except Exception as e:
            logger.error(f"Bark notify error: {e.__class__.__name__}: {e}")
            return False

notifier = BarkNotifier()

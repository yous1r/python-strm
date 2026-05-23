import httpx
import json
import base64
from urllib.parse import quote
from loguru import logger
from app.config import get_config

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

class BarkNotifier:
    def __init__(self):
        # We fetch config at call time to support hot-reload properly
        pass

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

    async def send_message(self, content: str, title: str = "系统通知"):
        config = get_config().notify.bark
        if not config.enabled:
            return
        if not config.server or not config.device_key:
            logger.error("Bark notify is enabled but server or device_key is missing.")
            return

        server = config.server.rstrip('/')
        url = f"{server}/push"
        
        # 基础推送数据
        payload = {
            "title": title,
            "body": content
        }
        
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
                    post_data["device_key"] = config.device_key
                else:
                    # 明文 POST 推送 (更规范的方式)
                    post_data = payload.copy()
                    post_data["device_key"] = config.device_key
                    
                res = await client.post(url, json=post_data)
                data = res.json()
                if data.get("code") != 200:
                    logger.error(f"Bark notify failed: {data.get('message')}")
        except Exception as e:
            logger.exception("Bark notify error details")

notifier = BarkNotifier()

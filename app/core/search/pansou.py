import httpx
from loguru import logger
from typing import List, Dict, Any, Optional

PANSOU_API_URL = "http://pansou:8888/api/search"

class PansouClient:
    def __init__(self):
        self.timeout = 15.0  # Pansou might take some time to query all plugins

    async def search(self, keyword: str, source_type: str = "all") -> Dict[str, Any]:
        """
        向底层 Pansou 搜索引擎发起搜索请求
        source_type: "tg", "plugin", "all"
        """
        params = {
            "kw": keyword,
            "src": source_type,
            "res": "results" # 直接返回结果列表
        }
        
        logger.info(f"🔍 正在向 Pansou 引擎搜索关键字: {keyword}")
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(PANSOU_API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                if data.get("code") == 0:
                    search_data = data.get("data", {})
                    results = search_data.get("results", [])
                    logger.info(f"✅ Pansou 搜索成功，共命中 {len(results)} 条结果")
                    return search_data
                else:
                    logger.error(f"❌ Pansou 返回错误: {data.get('message')}")
                    return {}
        except Exception as e:
            logger.error(f"❌ 请求 Pansou 失败: {e}")
            return {}

pansou_client = PansouClient()

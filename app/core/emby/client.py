import httpx
from loguru import logger
from app.core.emby.manager import emby_manager

class EmbyClient:
    async def get_admin_user_id(self, instance: dict) -> str:
        """获取管理员的用户ID"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    f"{instance['url']}/emby/Users",
                    params={"api_key": instance['api_key']}
                )
                if res.status_code == 200:
                    users = res.json()
                    # 优先返回具有管理权限的用户
                    for user in users:
                        if user.get("Policy", {}).get("IsAdministrator"):
                            return user.get("Id")
                    # fallback
                    if users:
                        return users[0].get("Id")
        except Exception as e:
            logger.error(f"Failed to get emby users: {e}")
        return ""

    async def get_series(self, instance_id: str) -> list[dict]:
        """获取全部剧集信息 (Series)"""
        instance = await emby_manager.get_instance(instance_id)
        if not instance:
            return []
            
        user_id = await self.get_admin_user_id(instance)
        if not user_id:
            return []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(
                    f"{instance['url']}/emby/Users/{user_id}/Items",
                    params={
                        "api_key": instance['api_key'],
                        "IncludeItemTypes": "Series",
                        "Recursive": "true",
                        "Fields": "Overview,PremiereDate"
                    }
                )
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("Items", [])
                    
                    results = []
                    for item in items:
                        item_id = item.get("Id")
                        poster_url = ""
                        if item.get("ImageTags", {}).get("Primary"):
                            poster_url = f"{instance['url']}/emby/Items/{item_id}/Images/Primary?api_key={instance['api_key']}"
                            
                        results.append({
                            "id": item_id,
                            "name": item.get("Name"),
                            "overview": item.get("Overview", ""),
                            "year": item.get("ProductionYear", ""),
                            "poster_url": poster_url,
                        })
                    return results
        except Exception as e:
            logger.error(f"Failed to get emby series: {e}")
        return []

    async def get_episodes(self, instance_id: str, series_id: str) -> list[dict]:
        """获取指定剧集的单集列表"""
        instance = await emby_manager.get_instance(instance_id)
        if not instance:
            return []
            
        user_id = await self.get_admin_user_id(instance)
        if not user_id:
            return []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(
                    f"{instance['url']}/emby/Users/{user_id}/Items",
                    params={
                        "api_key": instance['api_key'],
                        "IncludeItemTypes": "Episode",
                        "ParentId": series_id,
                        "Recursive": "true",
                        "Fields": "Overview"
                    }
                )
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("Items", [])
                    
                    results = []
                    for item in items:
                        item_id = item.get("Id")
                        poster_url = ""
                        if item.get("ImageTags", {}).get("Primary"):
                            poster_url = f"{instance['url']}/emby/Items/{item_id}/Images/Primary?api_key={instance['api_key']}"
                            
                        results.append({
                            "id": item_id,
                            "name": item.get("Name"),
                            "season": item.get("ParentIndexNumber", 1),
                            "episode": item.get("IndexNumber", 1),
                            "overview": item.get("Overview", ""),
                            "poster_url": poster_url,
                        })
                        
                    # 排序：按季和集号
                    results.sort(key=lambda x: (x["season"], x["episode"]))
                    return results
        except Exception as e:
            logger.error(f"Failed to get emby episodes: {e}")
        return []

    async def refresh_library(self, instance_id: str) -> bool:
        """全局触发 Emby 扫描库"""
        instance = await emby_manager.get_instance(instance_id)
        if not instance:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(
                    f"{instance['url']}/emby/Library/Refresh",
                    params={"api_key": instance['api_key']}
                )
                if res.status_code in (200, 204):
                    logger.info(f"Successfully triggered Emby library refresh for instance {instance['name']}")
                    return True
                else:
                    logger.warning(f"Emby library refresh returned status {res.status_code}")
                    return False
        except Exception as e:
            logger.error(f"Failed to refresh Emby library: {e}")
            return False

emby_client = EmbyClient()

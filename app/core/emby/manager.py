import httpx
from loguru import logger
from app.database import get_db_conn
import uuid

class EmbyManager:
    async def add_instance(self, name: str, url: str, api_key: str) -> dict:
        instance_id = str(uuid.uuid4())
        url = url.rstrip('/')
        
        async with get_db_conn() as db:
            await db.execute(
                'INSERT INTO emby_instances (id, name, url, api_key) VALUES (?, ?, ?, ?)',
                (instance_id, name, url, api_key)
            )
            await db.commit()
            
        return {"id": instance_id, "name": name, "url": url}

    async def get_instances(self) -> list:
        async with get_db_conn() as db:
            async with db.execute('SELECT * FROM emby_instances') as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_instance(self, instance_id: str) -> dict:
        async with get_db_conn() as db:
            async with db.execute('SELECT * FROM emby_instances WHERE id = ?', (instance_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def test_connection(self, instance_id: str) -> bool:
        instance = await self.get_instance(instance_id)
        if not instance:
            return False
            
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    f"{instance['url']}/System/Info",
                    params={"api_key": instance['api_key']}
                )
                return res.status_code == 200
        except Exception as e:
            logger.error(f"Emby connection test failed: {e}")
            return False

emby_manager = EmbyManager()

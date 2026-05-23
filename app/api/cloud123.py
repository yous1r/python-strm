from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from app.core.cloud123.client import client_123
from app.core.cloud123.auth import auth_manager_123

router = APIRouter(prefix="/api/v1/123", tags=["123网盘"])

class AuthRequest(BaseModel):
    token: str

@router.post("/auth")
async def update_token(req: AuthRequest):
    """更新123 Token"""
    success = auth_manager_123.update_token(req.token)
    if success:
        return {"status": "success", "message": "Token updated"}
    raise HTTPException(status_code=400, detail="Invalid token")

@router.get("/user")
async def get_user():
    """获取用户信息"""
    info = await auth_manager_123.get_user_info()
    if "error" in info:
        raise HTTPException(status_code=400, detail=info["error"])
    return info

@router.get("/files")
async def list_files(parent_id: str = '0', limit: int = 100, page: int = 1):
    """获取文件列表"""
    res = await client_123.list_files(parent_id, limit, page)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@router.get("/play/{file_id}")
async def play_video(file_id: str):
    """获取视频直链并302跳转"""
    url = await client_123.get_download_url(file_id)
    if not url:
        raise HTTPException(status_code=404, detail="Download URL not found")
    return RedirectResponse(url=url, status_code=302)

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from app.config import get_config
import os

router = APIRouter(tags=["Web UI"])
templates = Jinja2Templates(directory="app/web/templates")

@router.get("/")
async def dashboard(request: Request):
    config = get_config()
    return templates.TemplateResponse(request, "dashboard.html", {
        "config": config,
        "title": "仪表盘"
    })

@router.get("/strm")
async def strm_page(request: Request):
    return templates.TemplateResponse(request, "strm.html", {
        "title": "STRM管理"
    })
    
@router.get("/115")
async def page_115(request: Request):
    config = get_config()
    return templates.TemplateResponse(request, "115.html", {
        "config": config,
        "title": "115网盘"
    })

@router.get("/123")
async def page_123(request: Request):
    config = get_config()
    return templates.TemplateResponse(request, "123.html", {
        "config": config,
        "title": "123网盘"
    })

@router.get("/emby")
async def page_emby(request: Request):
    return templates.TemplateResponse(request, "emby.html", {
        "title": "Emby 媒体库"
    })

@router.get("/notify")
async def page_notify(request: Request):
    return templates.TemplateResponse(request, "notify.html", {
        "title": "消息推送"
    })

@router.get("/organize")
async def page_organize(request: Request):
    return templates.TemplateResponse(request, "organize.html", {
        "title": "刮削与整理"
    })

@router.get("/advanced")
async def page_advanced(request: Request):
    config = get_config()
    return templates.TemplateResponse(request, "advanced.html", {
        "config": config,
        "title": "全局高阶配置"
    })

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.cloud import get_cloud_plugin
from app.core.cloud.plugin import CloudPluginNotFoundError
from app.config import get_config

router = APIRouter(prefix="/api/v1/strm", tags=["STRM管理"])

class GenerateReq(BaseModel):
    cloud_type: str  # 115
    dir_id: str
    recursive: bool = True

@router.post("/generate")
async def generate_strm(req: GenerateReq):
    config = get_config()
    output_dir = config.strm.output_dir
    base_url = config.strm.base_url
    
    try:
        plugin = get_cloud_plugin(req.cloud_type)
    except CloudPluginNotFoundError:
        raise HTTPException(status_code=400, detail="Unsupported cloud type")
    generated = await plugin.strm_generator.batch_generate(req.dir_id, output_dir, base_url, req.recursive)
    
    return {
        "status": "success",
        "generated_count": len(generated),
        "files": generated[:10]  # 只返回前10个作为预览
    }

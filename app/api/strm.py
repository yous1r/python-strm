from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.cloud115.strm import generator_115
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
    
    if req.cloud_type == "115":
        generated = await generator_115.batch_generate(req.dir_id, output_dir, base_url, req.recursive)
    elif req.cloud_type == "123":
        from app.core.cloud123.strm import generator_123
        generated = await generator_123.batch_generate(req.dir_id, output_dir, base_url, req.recursive)
    else:
        raise HTTPException(status_code=400, detail="Unsupported cloud type")
    
    return {
        "status": "success",
        "generated_count": len(generated),
        "files": generated[:10]  # 只返回前10个作为预览
    }

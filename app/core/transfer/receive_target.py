"""转存前解析分享内容并准备最终归档目录。"""

from dataclasses import dataclass

from app.core.cloud import get_cloud_plugin
from app.core.cloud.plugin import CloudClientProtocol

from .classifier import build_archive_path, classify as default_classify


@dataclass(frozen=True)
class ReceiveTarget:
    target_dir_id: str
    archive_rel_path: str
    share_files: list[dict]
    classified: bool = False


async def prepare_receive_target(
    *,
    share_url: str,
    receive_code: str = "",
    archive_dir_id: str,
    fallback_dir_id: str = "",
    cloud_type: str = "115",
    cloud_client: CloudClientProtocol | None = None,
    classifier=default_classify,
) -> ReceiveTarget:
    """按分类规则预创建最终目录，失败时回退到显式传入的目标目录。"""
    client = cloud_client or get_cloud_plugin(cloud_type).client
    share_info = await client.get_share_info(share_url, receive_code)
    share_files = list(share_info.get("files") or [])

    if archive_dir_id and archive_dir_id != "0" and share_files:
        sample_name = _pick_sample_name(share_files)
        if sample_name:
            classify_result = await classifier(sample_name)
            if classify_result:
                archive_rel_path = "/".join(build_archive_path(classify_result))
                create_res = await client.create_path(archive_dir_id, archive_rel_path)
                target_dir_id = str(create_res.get("id") or "")
                if target_dir_id:
                    return ReceiveTarget(
                        target_dir_id=target_dir_id,
                        archive_rel_path=archive_rel_path,
                        share_files=share_files,
                        classified=True,
                    )

    return ReceiveTarget(
        target_dir_id=fallback_dir_id,
        archive_rel_path=await infer_archive_rel_path(share_files, classifier=classifier),
        share_files=share_files,
        classified=False,
    )


async def infer_archive_rel_path(
    share_files: list[dict],
    *,
    classifier=default_classify,
) -> str:
    sample_name = _pick_sample_name(share_files)
    if not sample_name:
        return ""

    classify_result = await classifier(sample_name)
    if not classify_result:
        return ""

    return "/".join(build_archive_path(classify_result))


def _pick_sample_name(share_files: list[dict]) -> str:
    for item in share_files:
        name = str(item.get("name") or item.get("n") or "").strip()
        if name:
            return name
    return ""


__all__ = ["ReceiveTarget", "infer_archive_rel_path", "prepare_receive_target"]

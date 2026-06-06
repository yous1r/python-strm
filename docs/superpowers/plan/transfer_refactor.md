---
name: local-cache-strm-optimization
overview: 整剧转存优化：先用一个文件名 TMDB 分类得到完整路径，fs_makedirs_app 一次创建，N 个 share_receive 落入最终目录，批量生成 STRM。
todos:
  - id: i1
    content: "client.py: share_receive 返回 share_files [{name, sha}]"
    status: completed
  - id: i2
    content: "library.py: classify+create_path+间隔emit+传season_folder_cid"
    status: pending
  - id: i3
    content: "strm.py: generate_strm_for_folder 批量SHA1匹配生成"
    status: completed
  - id: i4
    content: "handler.py: 累积share_files到批次，完成时批量STRM"
    status: pending
  - id: i5
    content: 提交推送
    status: pending
isProject: false
---

## 流程

30 集剧 `主角`，取第一集文件名做 TMDB 分类：

```
1. classify("主角.S01E01.mp4") → 本地: 剧集/国产剧集/主角（2026）{tmdb-289271}/Season 1
2. create_path(archive_dir_id, "剧集/国产剧集/主角（2026）{tmdb-289271}/Season 1") → 1 次 API: fs_makedirs_app 一次创建完整路径，拿到 season_folder_cid
3. share_receive(每集链接, cid=season_folder_cid) → N 次 API: 间隔 0.1s
4. 本地: generate_strm_for_folder(season_folder_cid, share_files)
5. 本地: 写入 strm_output/剧集/国产剧集/主角（2026）{tmdb-289271}/Season 1/*.strm
```

## API 调用数

| 步骤 | 次数 |
|---|---|
| classify（本地 TMDB 搜索 + 路径生成） | 0 |
| create_path（fs_makedirs_app 一次建完整路径） | 1 |
| share_receive × N | N |
| list_files（用于 STRM 生成） | 1 |

总计: N + 2 次 API
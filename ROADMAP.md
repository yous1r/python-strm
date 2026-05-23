# Python-STRM 项目路线图与后续规划 (Roadmap)

本文档记录了项目中已规划但尚未执行的功能需求和架构演进计划，作为后续开发备忘。

## 📍 待处理需求 (Backlog)

### 1. 扩展网盘客户端接入：阿里云盘 & 夸克网盘
目前系统已成功接入 115网盘和 123云盘，未来计划将网盘支持扩展至阿里云盘和夸克网盘。以下为技术预研和接入参考：

#### 🔹 阿里云盘 (Aliyun Drive) 
* **优先级**: 高
* **接入难度**: 🟢 容易
* **技术方案**: 
  * 建议基于成熟的开源库 [foyoux/aligo](https://github.com/foyoux/aligo)（极度推荐）进行封装，其支持完整的扫码登录、目录列表、秒传与直链解析机制。
  * 若要坚持项目级别的原生异步，可参考 [N0I0C0K/async-aliyun-disk-sdk](https://github.com/N0I0C0K/async-aliyun-disk-sdk) 进行 `asyncio` 的完全适配。
* **目标**: 增加 `CloudAliClient`，并在前端 STRM 生成页面增加"阿里云盘"选项。

#### 🔹 夸克网盘 (Quark Drive)
* **优先级**: 中
* **接入难度**: 🟡 中等（需应对防爬风控）
* **技术方案**:
  * 夸克网盘第三方生态主要依靠逆向抓包。建议参考 [ihmily/QuarkPanTool](https://github.com/ihmily/QuarkPanTool) 提取其 API 交互核心逻辑（维护 Cookie、获取列表与转存解析）。
  * 或者参考独立的 Python 封装 [lich0821/QuarkPan](https://github.com/lich0821/QuarkPan)。
* **目标**: 增加 `CloudQuarkClient`。需要注意在高频生成 STRM 时的 API 速率控制与账户风控避免。

---

> 提示：当需要开发上述功能时，可以直接告知 AI 代理：“开始处理路线图中的阿里云盘接入需求”。

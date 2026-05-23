# Telegram 与 Bark 消息推送及统一通知管理器设计规范

## 1. 目标描述
将现有的系统通知机制升级为**插件化、统一化**的消息推送服务。除了目前预留的企业微信 (WeCom) 通道外，新增 **Telegram Bot 推送** 和 **Bark 推送**，允许用户通过任一或所有已启用的渠道接收系统产生的通知消息。

## 2. 架构设计与组件职责

我们将采用**并发插件式**架构：

* **核心模块 (`app/core/notify/manager.py`)**:
  * 定义统一的 `NotificationManager` 单例。
  * 暴露 `async def send(title: str, content: str)` 方法。
  * 读取所有可用的推送提供者（WeCom, Telegram, Bark），检查其 `enabled` 状态。
  * 对于开启的渠道，使用 `asyncio.gather` 并发下发消息，任何单一渠道的超时或报错均不会阻塞其他渠道的推送。

* **配置模块 (`config.yaml` & `app/config.py`)**:
  * 在 `NotifyConfig` 中增加 `telegram` 和 `bark` 子节点，明确区分用于**接收推送的 Bot 凭证**与用于监控频道的凭证。

* **具体的推送客户端**:
  * **WeCom (`wecom.py`)**: 维持现有实现，负责通过企微应用下发消息。
  * **Telegram (`telegram.py`)**: 使用 `httpx` 调用 Telegram Bot API `sendMessage` 接口向指定的 `chat_id` 发送消息。
  * **Bark (`bark.py`)**: 构造形如 `GET {server}/{device_key}/{title}/{content}` 的原生请求完成 iOS 设备的直接推送通知。

## 3. 配置数据结构

```yaml
notify:
  wecom:
    enabled: false
    corp_id: ""
    corp_secret: ""
    agent_id: ""
  telegram:
    enabled: false
    bot_token: ""    # 用于推送的 Bot Token
    chat_id: ""      # 接收消息的用户或群组 ID
  bark:
    enabled: false
    server: "https://api.day.app"
    device_key: ""
```

## 4. 容错与异常处理
* **非阻塞模型**：通知是系统主要业务的副产品。`NotificationManager` 的错误会被 `loguru` 捕获并记录日志，绝不向外抛出异常以至于中断主流程（如中断 STRM 文件的生成或定时任务）。
* **网络超时**：各路推送客户端均应设置明确的超时机制（如 `timeout=10` 秒），避免长挂起。
* **空值判断**：若 `enabled=true` 但必填配置（如 token）为空，各客户端内部拦截并记录错误，但不引发系统崩溃。

## 5. 验收标准
1. `config.yaml` 和 `app/config.py` 配置模型升级完毕，可同时并存并管理 `wecom`, `telegram`, `bark`。
2. 调用统一管理器接口 `notify_manager.send()` 能够并发触发多路请求。
3. `app/core/notify/telegram.py` 和 `app/core/notify/bark.py` 均正确使用了异步非阻塞 HTTP 库。

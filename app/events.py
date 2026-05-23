import asyncio
from typing import Callable, Dict, List, Any
from loguru import logger

class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable):
        """订阅事件"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)
            logger.debug(f"Subscribed to {event_type}")

    def unsubscribe(self, event_type: str, callback: Callable):
        """取消订阅"""
        if event_type in self._subscribers and callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)

    async def emit(self, event_type: str, **kwargs):
        """发布事件"""
        if event_type in self._subscribers:
            logger.debug(f"Emitting event {event_type} with data: {kwargs}")
            callbacks = self._subscribers[event_type]
            # 异步执行所有回调
            tasks = [asyncio.create_task(self._safe_call(cb, **kwargs)) for cb in callbacks]
            if tasks:
                await asyncio.gather(*tasks)

    async def _safe_call(self, callback: Callable, **kwargs):
        """安全调用回调函数，捕获异常"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(**kwargs)
            else:
                callback(**kwargs)
        except Exception as e:
            logger.error(f"Error in event callback {callback.__name__}: {e}")

# 全局单例
event_bus = EventBus()

# 预定义事件常量
EVENT_ORGANIZE_COMPLETE = "organize_complete"
EVENT_STRM_GENERATED = "strm_generated"
EVENT_TRANSFER_COMPLETE = "transfer_complete"
EVENT_MONITOR_NEW_LINK = "monitor_new_link"

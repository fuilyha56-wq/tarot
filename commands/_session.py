"""问答模式下的待回答会话存储。

按 stream_id 维护"等待用户回答"的状态。同一聊天流同时只允许一个 pending session。
带超时清理：每次访问时顺手清掉过期项，无需后台任务。

设计原则：进程内、内存级、单实例。重启即清空，符合"占卜本应短时"的语义。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class PendingSession:
    """单条 pending 会话状态。

    Attributes:
        stream_id: 聊天流 ID
        sender_id: 发起占卜的用户 ID（用于回答时匹配是同一人）
        platform: 平台标识，回填发消息用
        questions_text: actor 提的三个问题原文（用于 LLM 复盘 / 调试）
        created_at: 创建时间戳（秒）
    """

    stream_id: str
    sender_id: str
    platform: str
    questions_text: str
    created_at: float = field(default_factory=time.time)


class PendingSessionStore:
    """以 stream_id 为键的 pending 会话表。线程安全。"""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        """初始化存储。

        Args:
            ttl_seconds: 会话有效期；超过则视为超时
        """
        self._ttl = ttl_seconds
        self._sessions: dict[str, PendingSession] = {}
        self._lock = Lock()

    def set_ttl(self, ttl_seconds: float) -> None:
        """运行时调整 TTL（用于配置热更）。"""
        self._ttl = ttl_seconds

    @property
    def ttl(self) -> float:
        """当前 TTL（秒）。"""
        return self._ttl

    def _is_expired(self, session: PendingSession, now: float) -> bool:
        """判断是否过期。"""
        return (now - session.created_at) > self._ttl

    def put(self, session: PendingSession) -> PendingSession | None:
        """注册一条新会话；若已存在同流的 pending，旧的会被替换并返回。

        Args:
            session: 新会话

        Returns:
            被替换的旧会话；若无则返回 None
        """
        with self._lock:
            old = self._sessions.get(session.stream_id)
            self._sessions[session.stream_id] = session
            return old

    def pop_if_match(self, stream_id: str, sender_id: str) -> PendingSession | None:
        """若该流有 pending 且发送者一致且未过期，弹出并返回；否则 None。

        Args:
            stream_id: 聊天流 ID
            sender_id: 发送者 ID

        Returns:
            匹配的会话；不匹配 / 过期 / 不存在均返回 None
        """
        now = time.time()
        with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                return None
            if self._is_expired(session, now):
                self._sessions.pop(stream_id, None)
                return None
            if session.sender_id != sender_id:
                return None
            return self._sessions.pop(stream_id)

    def discard(self, stream_id: str) -> PendingSession | None:
        """无条件移除指定流的 pending 会话。

        Args:
            stream_id: 聊天流 ID

        Returns:
            被移除的会话；不存在则 None
        """
        with self._lock:
            return self._sessions.pop(stream_id, None)

    def cleanup_expired(self) -> int:
        """清理所有已超时的会话。

        Returns:
            被清理的条数
        """
        now = time.time()
        with self._lock:
            expired_ids = [
                sid for sid, s in self._sessions.items() if self._is_expired(s, now)
            ]
            for sid in expired_ids:
                self._sessions.pop(sid, None)
            return len(expired_ids)


# 模块级单例：所有命令 / 事件处理器共用
SESSION_STORE = PendingSessionStore()

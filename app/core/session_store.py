"""
Distributed Redis Call Session Store for QwickCall
Provides shared, multi-worker persistent session storage for Asterisk/WebRTC and SIP calls.
Falls back to in-memory dictionary if Redis is not configured or offline.
"""
import json
import time
from typing import Dict, Any, Optional

from app.config import settings

# In-memory fallback dictionary with TTL and bounded capacity
_MEM_SESSIONS: Dict[str, Dict[str, Any]] = {}
_MEM_SESSIONS_EXPIRY: Dict[str, float] = {}
MAX_MEM_SESSIONS: int = 2000

_SYNC_REDIS = None
_ASYNC_REDIS = None


def _evict_expired_mem_sessions():
    now = time.time()
    expired = [cid for cid, exp in _MEM_SESSIONS_EXPIRY.items() if exp < now]
    for cid in expired:
        _MEM_SESSIONS.pop(cid, None)
        _MEM_SESSIONS_EXPIRY.pop(cid, None)
    # If still above capacity, evict oldest 20%
    if len(_MEM_SESSIONS) > MAX_MEM_SESSIONS:
        sorted_cids = sorted(_MEM_SESSIONS_EXPIRY.items(), key=lambda x: x[1])
        for cid, _ in sorted_cids[:len(sorted_cids) // 5]:
            _MEM_SESSIONS.pop(cid, None)
            _MEM_SESSIONS_EXPIRY.pop(cid, None)


def _get_sync_redis():
    global _SYNC_REDIS
    if not settings.REDIS_URL:
        return None
    if _SYNC_REDIS is None:
        try:
            import redis
            _SYNC_REDIS = redis.from_url(settings.REDIS_URL, decode_responses=True)
            _SYNC_REDIS.ping()
        except Exception as e:
            print(f"[SessionStore] Sync Redis connection warning: {e}")
            _SYNC_REDIS = None
    return _SYNC_REDIS


def _get_async_redis():
    global _ASYNC_REDIS
    if not settings.REDIS_URL:
        return None
    if _ASYNC_REDIS is None:
        try:
            import redis.asyncio as aioredis
            _ASYNC_REDIS = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception as e:
            print(f"[SessionStore] Async Redis connection warning: {e}")
            _ASYNC_REDIS = None
    return _ASYNC_REDIS


def _make_key(call_id: str) -> str:
    return f"call_session:{call_id.strip()}"


import concurrent.futures

# Dedicated background executor for non-blocking Redis session persistence
_SESSION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="redis_session_saver")

import copy

def save_session(call_id: str, data: Dict[str, Any], ttl: int = 3600) -> bool:
    """Save session data instantly to local memory and persist to Redis in background."""
    if not call_id:
        return False
    _evict_expired_mem_sessions()
    _MEM_SESSIONS[call_id] = data
    _MEM_SESSIONS_EXPIRY[call_id] = time.time() + ttl
    if settings.REDIS_URL:
        frozen_data = copy.deepcopy(data)
        def _bg_save(payload):
            global _SYNC_REDIS
            try:
                r = _get_sync_redis()
                if r:
                    r.setex(_make_key(call_id), ttl, json.dumps(payload, default=str))
            except Exception as e:
                _SYNC_REDIS = None
                print(f"[SessionStore] Error in background Redis save: {e}")
        try:
            _SESSION_EXECUTOR.submit(_bg_save, frozen_data)
        except Exception:
            pass
    return True


def get_session(call_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve session data instantly from local memory; fallback to Redis only on cache miss."""
    global _SYNC_REDIS
    if not call_id:
        return None
    if call_id in _MEM_SESSIONS:
        return _MEM_SESSIONS[call_id]
    r = _get_sync_redis()
    if r:
        try:
            raw = r.get(_make_key(call_id))
            if raw:
                parsed = json.loads(raw)
                _MEM_SESSIONS[call_id] = parsed
                return parsed
        except Exception as e:
            _SYNC_REDIS = None
            print(f"[SessionStore] Error reading session from Redis: {e}")
    return None


def update_session(call_id: str, updates: Dict[str, Any], ttl: int = 3600) -> Optional[Dict[str, Any]]:
    """Update fields in an existing session."""
    current = get_session(call_id) or {}
    current.update(updates)
    save_session(call_id, current, ttl=ttl)
    return current


async def save_session_async(call_id: str, data: Dict[str, Any], ttl: int = 3600) -> bool:
    """Save session asynchronously."""
    global _ASYNC_REDIS
    if not call_id:
        return False
    _MEM_SESSIONS[call_id] = data
    r = _get_async_redis()
    if r:
        try:
            await r.setex(_make_key(call_id), ttl, json.dumps(data, default=str))
            return True
        except Exception as e:
            _ASYNC_REDIS = None
            print(f"[SessionStore] Async Redis save error: {e}")
    return True


async def get_session_async(call_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve session asynchronously."""
    global _ASYNC_REDIS
    if not call_id:
        return None
    r = _get_async_redis()
    if r:
        try:
            raw = await r.get(_make_key(call_id))
            if raw:
                parsed = json.loads(raw)
                _MEM_SESSIONS[call_id] = parsed
                return parsed
        except Exception as e:
            _ASYNC_REDIS = None
            print(f"[SessionStore] Async Redis get error: {e}")
    return _MEM_SESSIONS.get(call_id)


async def update_session_async(call_id: str, updates: Dict[str, Any], ttl: int = 3600) -> Optional[Dict[str, Any]]:
    """Update session asynchronously."""
    current = await get_session_async(call_id) or {}
    current.update(updates)
    await save_session_async(call_id, current, ttl=ttl)
    return current


def delete_session(call_id: str):
    """Remove session from Redis and memory."""
    _MEM_SESSIONS.pop(call_id, None)
    r = _get_sync_redis()
    if r:
        try:
            r.delete(_make_key(call_id))
        except Exception:
            pass


# Distributed proxy wrapper mimicking dict access for backward compatibility
class SessionProxy(dict):
    def __getitem__(self, key):
        sess = get_session(str(key))
        if sess is None:
            raise KeyError(key)
        return sess

    def __setitem__(self, key, value):
        save_session(str(key), value)

    def get(self, key, default=None):
        sess = get_session(str(key))
        return sess if sess is not None else default

    def __contains__(self, key):
        return get_session(str(key)) is not None

    def pop(self, key, default=None):
        val = self.get(key, default)
        delete_session(str(key))
        return val

    def keys(self):
        _evict_expired_mem_sessions()
        return list(_MEM_SESSIONS.keys())

    def values(self):
        return [self.get(k) for k in self.keys() if self.get(k) is not None]

    def items(self):
        _evict_expired_mem_sessions()
        res = []
        for k in list(_MEM_SESSIONS.keys()):
            val = self.get(k)
            if val is not None:
                res.append((k, val))
        return res

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        _evict_expired_mem_sessions()
        return len(_MEM_SESSIONS)


# Global shared sessions instance that works like SESSIONS dict but persists to Redis!
DISTRIBUTED_SESSIONS = SessionProxy()

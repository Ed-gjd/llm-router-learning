#!/usr/bin/env python3
"""lib/ratelimit.py — 分布式限流（课4.x）

把单进程 RPM 限速升级为多实例共享配额：
    - Redis 后端：Lua 脚本原子判定（滑动窗口 / 令牌桶 / 并发租约），服务器时间消时钟偏差
    - 本地兜底：无 Redis（或 Redis 故障）自动降级为进程内线程安全限速，主链路不中断
    - 策略：config/ratelimit.yaml 规则表驱动；fail=local 兜底 / closed 拒绝

用法:
    from lib import ratelimit as rl
    d = rl.limiter.check("ip", "1.2.3.4")            # 规则默认限额
    d = rl.limiter.check("provider", "kimi", limit=3, window=60)
    d = rl.limiter.wait("provider", "kimi", limit=3, window=60, timeout=30)
    ok, lease = rl.limiter.acquire("agent", "claude")  # 并发租约
    lease.release()
"""
import math
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class RateLimited(Exception):
    """超限且拒绝等待（wait=False）时抛出。"""


class BackendDown(Exception):
    """Redis 后端运行期故障。"""


@dataclass
class Decision:
    ok: bool
    remaining: float = 0.0
    retry_after: float = 0.0
    count: float = 0.0
    backend: str = "local"
    detail: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "remaining": round(self.remaining, 3),
                "retry_after": round(self.retry_after, 3), "count": round(self.count, 3),
                "backend": self.backend, "detail": self.detail}


# ---------- Lua 脚本：单 key、原子、服务器时间 ----------

_LUA_SLIDING_WINDOW = """
-- 滑动窗口计数（混合）：count = prev * overlap + curr
-- KEYS[1]=计数键  ARGV: limit window cost
local now = tonumber(redis.call('TIME')[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[1])
local cost = tonumber(ARGV[3])
local idx_k, prev_k, curr_k = KEYS[1]..':idx', KEYS[1]..':prev', KEYS[1]..':curr'
local idx = math.floor(now / window)
local cur_idx = tonumber(redis.call('GET', idx_k) or '0')
local prev = tonumber(redis.call('GET', prev_k) or '0')
local curr = tonumber(redis.call('GET', curr_k) or '0')
if idx > cur_idx then
    if idx - cur_idx == 1 then redis.call('SET', prev_k, curr)
    else redis.call('SET', prev_k, 0) end
    redis.call('SET', curr_k, 0)
    redis.call('SET', idx_k, idx)
end
local overlap = 1 - (now - idx * window) / window
local count = prev * overlap + curr
if count + cost > limit then
    return {0, count + cost - limit, count}
end
redis.call('INCRBY', curr_k, cost)
redis.call('PEXPIRE', curr_k, window * 2000 + 1000)
redis.call('PEXPIRE', prev_k, window * 2000 + 1000)
redis.call('PEXPIRE', idx_k, window * 2000 + 1000)
return {1, limit - count - cost, count + cost}
"""

_LUA_TOKEN_BUCKET = """
-- 令牌桶：rate=每秒补充, burst=桶容量, cost=本次消耗
-- KEYS[1]=桶键  ARGV: rate burst cost
local now = tonumber(redis.call('TIME')[1])
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local d = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(d[1] or burst)
local ts = tonumber(d[2] or now)
tokens = math.min(burst, tokens + (now - ts) * rate)
if tokens + 1e-9 < cost then
    return {0, math.ceil((cost - tokens) / rate), tokens}
end
tokens = tokens - cost
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], 120000)
return {1, tokens, tokens}
"""

_LUA_SEM_ACQUIRE = """
-- 并发租约：hash 存 holder->到期时间；过期 holder 先清理再计数
-- KEYS[1]=信号量键  ARGV: holder max lease
local now = tonumber(redis.call('TIME')[1])
local holder, max, lease = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
for _, h in ipairs(redis.call('HKEYS', KEYS[1])) do
    if tonumber(redis.call('HGET', KEYS[1], h) or '0') < now then
        redis.call('HDEL', KEYS[1], h)
    end
end
local n = redis.call('HLEN', KEYS[1])
if n >= max then
    return {0, n}
end
redis.call('HSET', KEYS[1], holder, now + lease)
redis.call('PEXPIRE', KEYS[1], lease * 2 + 1000)
return {1, n + 1}
"""

_LUA_SEM_RELEASE = """
-- 释放租约
redis.call('HDEL', KEYS[1], ARGV[1])
return {1}
"""

_LUA_SEM_RENEW = """
-- 续租
local now = tonumber(redis.call('TIME')[1])
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then
    redis.call('HSET', KEYS[1], ARGV[1], now + tonumber(ARGV[2]))
    redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2 + 1000)
end
return {1}
"""


# ---------- 本地兜底后端（线程安全，行为与 Redis 版一致） ----------

class LocalBackend:
    """进程内限速：滑动窗口(精确 deque)、令牌桶、并发租约。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._deques: dict[str, deque] = {}
        self._buckets: dict[str, tuple[float, float]] = {}
        self._holders: dict[str, dict[str, float]] = {}
        self._max_keys = 10_000

    def _prune(self, now: float) -> None:
        """防止长期不访问的键无限堆积。"""
        if len(self._deques) + len(self._buckets) + len(self._holders) <= self._max_keys:
            return
        for k, q in list(self._deques.items()):
            if not q or (q and now - q[-1] > 3600):
                del self._deques[k]
        for k, (_, ts) in list(self._buckets.items()):
            if now - ts > 3600:
                del self._buckets[k]
        for k, h in list(self._holders.items()):
            h = {x: dl for x, dl in h.items() if dl > now}
            if not h:
                del self._holders[k]
            else:
                self._holders[k] = h

    def sliding_window(self, key: str, limit: float, window: float, cost: float) -> Decision:
        with self._lock:
            now = time.time()
            self._prune(now)
            q = self._deques.setdefault(key, deque())
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) + cost > limit:
                wait = (q[0] - (now - window)) if q else window
                return Decision(False, remaining=max(0, limit - len(q)),
                                retry_after=max(1, math.ceil(wait)),
                                count=len(q), backend="local")
            for _ in range(int(cost)):
                q.append(now)
            return Decision(True, remaining=limit - len(q), count=len(q), backend="local")

    def token_bucket(self, key: str, rate: float, burst: float, cost: float) -> Decision:
        with self._lock:
            now = time.time()
            self._prune(now)
            tokens, ts = self._buckets.get(key, (burst, now))
            tokens = min(burst, tokens + (now - ts) * rate)
            if tokens + 1e-9 < cost:
                return Decision(False, remaining=tokens,
                                retry_after=max(1, math.ceil((cost - tokens) / rate)),
                                count=tokens, backend="local")
            tokens -= cost
            self._buckets[key] = (tokens, now)
            return Decision(True, remaining=tokens, count=tokens, backend="local")

    def acquire(self, key: str, holder: str, max_conc: int, lease: float) -> Decision:
        with self._lock:
            now = time.time()
            self._prune(now)
            h = self._holders.setdefault(key, {})
            for hh in [x for x, dl in h.items() if dl <= now]:
                del h[hh]
            if len(h) >= max_conc:
                wait = max((min(h.values()) - now), 0) if h else lease
                return Decision(False, count=len(h),
                                retry_after=max(1, math.ceil(wait)), backend="local")
            h[holder] = now + lease
            return Decision(True, count=len(h), remaining=max_conc - len(h), backend="local")

    def release(self, key: str, holder: str) -> None:
        with self._lock:
            self._holders.get(key, {}).pop(holder, None)

    def renew(self, key: str, holder: str, lease: float) -> None:
        with self._lock:
            h = self._holders.get(key, {})
            if holder in h:
                h[holder] = time.time() + lease


# ---------- Redis 后端 ----------

class RedisBackend:
    def __init__(self, client):
        self._client = client

    def _eval(self, script: str, key: str, *args):
        try:
            return self._client.eval(script, 1, key, *args)
        except Exception as e:
            raise BackendDown(f"redis eval 失败: {e}") from e

    def sliding_window(self, key: str, limit: float, window: float, cost: float) -> Decision:
        ok, extra, count = self._eval(_LUA_SLIDING_WINDOW, key, limit, window, cost)
        ok, extra, count = bool(ok), float(extra), float(count)
        if ok:
            return Decision(True, remaining=extra, count=count, backend="redis")
        return Decision(False, remaining=max(0, limit - count), count=count,
                        retry_after=max(1, math.ceil(extra * window / limit)), backend="redis")

    def token_bucket(self, key: str, rate: float, burst: float, cost: float) -> Decision:
        ok, extra, tokens = self._eval(_LUA_TOKEN_BUCKET, key, rate, burst, cost)
        ok, extra, tokens = bool(ok), float(extra), float(tokens)
        if ok:
            return Decision(True, remaining=tokens, count=tokens, backend="redis")
        return Decision(False, remaining=tokens, count=tokens,
                        retry_after=max(1, math.ceil(extra)), backend="redis")

    def acquire(self, key: str, holder: str, max_conc: int, lease: float) -> Decision:
        ok, n = self._eval(_LUA_SEM_ACQUIRE, key, holder, max_conc, lease)
        ok, n = bool(ok), int(n)
        if ok:
            return Decision(True, count=n, remaining=max_conc - n, backend="redis")
        return Decision(False, count=n, remaining=0,
                        retry_after=max(1, math.ceil(lease)), backend="redis")

    def release(self, key: str, holder: str) -> None:
        try:
            self._client.eval(_LUA_SEM_RELEASE, 1, key, holder)
        except Exception:
            pass  # 释放失败不阻塞业务；租约会自然过期

    def renew(self, key: str, holder: str, lease: float) -> None:
        try:
            self._client.eval(_LUA_SEM_RENEW, 1, key, holder, lease)
        except Exception:
            pass  # 续租失败：租约到期后自动回收


# ---------- 配置与门面 ----------

def load_config() -> dict:
    """读取 config/ratelimit.yaml；可用环境变量 RATELIMIT_CONFIG 指向其它文件。"""
    path = os.getenv("RATELIMIT_CONFIG") or (CONFIG_DIR / "ratelimit.yaml")
    try:
        import yaml
        raw = Path(path).read_text(encoding="utf-8")
        raw = os.path.expandvars(raw)  # 支持 ${ENV}
        return yaml.safe_load(raw) or {}
    except FileNotFoundError:
        return {}


class Lease:
    """并发租约：with 语句自动释放；长任务可 start_renewer 后台续租。"""

    def __init__(self, limiter: "RateLimiter", key: str, holder: str, lease: float):
        self._limiter = limiter
        self._key = key
        self._holder = holder
        self._lease = lease
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def renew(self) -> None:
        self._limiter._backend.renew(self._key, self._holder, self._lease)

    def start_renewer(self) -> None:
        """后台线程每 lease/3 续租一次，release 时停止。"""
        if self._thread is not None:
            return
        def _run():
            interval = max(self._lease / 3, 1.0)
            while not self._stop.wait(interval):
                self.renew()
        self._thread = threading.Thread(target=_run, daemon=True, name=f"lease-{self._holder[:8]}")
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._limiter._backend.release(self._key, self._holder)

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class RateLimiter:
    """分布式限流门面：维度 + 标识 → 判定；规则表驱动默认参数。

    backend: auto(优先 Redis，不可用降级本地) | redis(强制) | local
    fail:    local(Redis 故障降级本地) | closed(直接拒绝)
    """

    def __init__(self, config: dict | None = None):
        self._cfg = config if config is not None else load_config()
        self._rules = {r["dimension"]: r for r in self._cfg.get("rules", [])}
        self._backend: LocalBackend | RedisBackend | None = None
        self._lock = threading.Lock()
        self.degraded = False

    def _get_backend(self):
        with self._lock:
            if self._backend is not None:
                return self._backend
            mode = self._cfg.get("backend", "auto")
            if mode in ("auto", "redis"):
                try:
                    import redis as redis_mod
                    rc = self._cfg.get("redis", {})
                    client = redis_mod.Redis.from_url(
                        rc.get("url", "redis://127.0.0.1:6379/0"),
                        socket_connect_timeout=float(rc.get("socket_connect_timeout", 0.5)),
                        socket_timeout=float(rc.get("socket_timeout", 0.5)),
                        decode_responses=True,
                    )
                    client.ping()
                    self._backend = RedisBackend(client)
                    return self._backend
                except Exception as e:
                    if mode == "redis":
                        raise RuntimeError(f"backend=redis 强制但不可用: {e}") from e
                    print(f"[ratelimit] Redis 不可用（{e}），降级本地限速")
            self.degraded = True
            self._backend = LocalBackend()
            return self._backend

    def _call_backend(self, fn, *args):
        """运行期 Redis 故障：fail=closed 直接拒绝；否则降级本地并重试一次。"""
        try:
            return fn(*args)
        except BackendDown:
            if self._cfg.get("fail", "local") == "closed":
                return Decision(False, retry_after=1.0, backend="closed",
                                detail="redis-down-fail-closed")
            with self._lock:
                if isinstance(self._backend, RedisBackend):
                    print("[ratelimit] Redis 运行期故障，降级本地限速")
                    self._backend = LocalBackend()
                    self.degraded = True
            return fn(*args)

    def _rule(self, dimension: str) -> dict:
        return self._rules.get(dimension, {})

    @staticmethod
    def _key(algo: str, dimension: str, key: str) -> str:
        return f"rl:{algo}:{dimension}:{key}"

    def check(self, dimension: str, key: str, limit: float | None = None,
              window: float = 60, cost: float = 1, algorithm: str | None = None,
              rate: float | None = None, burst: float | None = None) -> Decision:
        """一次限流判定：allowed → 扣减；denied → 给出 retry_after。"""
        rule = self._rule(dimension)
        algorithm = (algorithm or rule.get("algorithm") or "sliding-window").lower()
        limit = limit if limit is not None else rule.get("limit")
        backend = self._get_backend()
        if algorithm == "token-bucket":
            r = rate if rate is not None else (limit / window if limit else 1.0)
            b = burst if burst is not None else rule.get("burst", max(1, int(r * window)))
            return self._call_backend(backend.token_bucket, self._key("tb", dimension, key), r, b, cost)
        if algorithm == "concurrency":
            d, _ = self.acquire(dimension, key)
            return d
        if limit is None or limit <= 0:
            return Decision(True, backend=backend.__class__.__name__.lower().replace("backend", ""),
                            detail="no-limit")
        return self._call_backend(backend.sliding_window, self._key("sw", dimension, key), limit, window, cost)

    def wait(self, dimension: str, key: str, limit: float | None = None,
             window: float = 60, cost: float = 1, algorithm: str | None = None,
             timeout: float = 60) -> Decision:
        """阻塞等待直到放行或超时（返回最后判定）。"""
        deadline = time.time() + timeout
        while True:
            d = self.check(dimension, key, limit=limit, window=window, cost=cost, algorithm=algorithm)
            if d.ok or time.time() >= deadline:
                return d
            time.sleep(min(max(d.retry_after, 0.05), 1.0))

    def acquire(self, dimension: str, key: str, max_concurrency: int | None = None,
                lease: float | None = None, timeout: float = 0) -> tuple[Decision, Lease | None]:
        """并发租约：timeout>0 时阻塞等位；返回 (判定, 租约)。"""
        rule = self._rule(dimension)
        max_conc = max_concurrency if max_concurrency is not None else rule.get("max_concurrency")
        lease = lease if lease is not None else rule.get("lease", 30.0)
        if max_conc is None or max_conc <= 0:
            return Decision(True, detail="no-limit"), None
        holder = uuid.uuid4().hex
        redis_key = self._key("sem", dimension, key)
        backend = self._get_backend()
        deadline = time.time() + timeout if timeout > 0 else None
        while True:
            d = self._call_backend(backend.acquire, redis_key, holder, max_conc, lease)
            if d.ok:
                return d, Lease(self, redis_key, holder, lease)
            if deadline is not None and time.time() < deadline:
                time.sleep(min(max(d.retry_after, 0.05), 1.0))
                continue
            return d, None

    def provider_throttle(self, provider_name: str, provider: dict, wait: bool = True) -> Decision | None:
        """provider 级 RPM 配额（providers.yaml 的 rpm 字段驱动）。"""
        rpm = int(provider.get("rpm", 0) or 0)
        if rpm <= 0:
            return None
        d = self.check("provider", provider_name, limit=rpm, window=60, algorithm="sliding-window")
        if d.ok:
            return d
        if not wait:
            raise RateLimited(f"{provider_name} RPM={rpm} 超限（retry_after={d.retry_after:.1f}s）")
        sleep_s = max(d.retry_after, 0.1)
        print(f"[限速] {provider_name} RPM={rpm}，sleep {sleep_s:.1f}s（{d.backend}）")
        time.sleep(sleep_s)
        return d


# 模块级单例：llm_client / gateway 直接复用
limiter = RateLimiter()

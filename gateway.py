#!/usr/bin/env python3
"""gateway.py — OpenAI 兼容 HTTP 网关（课3.1 + 课4.x 分布式限流）

一个 /v1/chat/completions 端点收编：三家模型 API + 全部 agent CLI。
任何 OpenAI 兼容客户端（curl / openai SDK）打到这一个 base_url，
网关按 model 字段内部分派：
    model="judge"        → 模型 API（router_v1 裁判路由）
    model="agent:xxx"    → agent CLI（agents.yaml 注册表，xxx=agent名）

分布式限流（config/ratelimit.yaml）：
    ip / global 维度超限 → 429 + Retry-After；
    agent 维度 → 租约式并发信号量（长任务自动续租）；
    provider RPM 配额在 llm_client 内等待（沿用原 sleep 语义）。

用法:
    python3 gateway.py &
    curl http://127.0.0.1:8123/v1/chat/completions \
         -H 'Content-Type: application/json' \
         -d '{"model":"judge","messages":[{"role":"user","content":"..."}]}'
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lib import obs
from lib import ratelimit as rl

PORT = 8123
HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")  # 课4.3：GATEWAY_HOST=0.0.0.0 暴露到局域网
LIMITER = rl.RateLimiter()


def route(model: str, task: str) -> dict:
    """按 model 字段分派：agent:xxx → agent CLI；否则 → 模型 API 裁判路由。"""
    if model.startswith("agent:"):
        from lib import agent_client as ac
        agent = model.split(":", 1)[1]
        r = ac.run_agent(agent, f"任务：{task}")   # mode 默认保守（护栏）
        ac.record_agent(r)
        r["kind"] = "agent"
        return r
    import router_v1 as rv
    r = rv.route_judge(task)
    r["kind"] = "llm"
    return r


CACHE: dict = {}
CACHE_MAX = 64


def _cached(model: str, task: str) -> tuple[dict, bool]:
    """精确匹配请求缓存（课C.2：Portkey 语义缓存的迷你版）。命中返回 True。"""
    key = (model, task)
    if key in CACHE:
        return CACHE[key], True
    r = route(model, task)
    CACHE[key] = r
    if len(CACHE) > CACHE_MAX:
        CACHE.pop(next(iter(CACHE)))
    return r, False


def to_openai_compat(r: dict) -> dict:
    """统一内部结果 → OpenAI 兼容响应结构。"""
    return {
        "choices": [{
            "message": {"role": "assistant",
                        "content": r.get("text") or r.get("reply") or r.get("error") or ""},
        }],
        "usage": {"prompt_tokens": r.get("tokens_in"),
                  "completion_tokens": r.get("tokens_out")},
        "model": r.get("used_model"),
        "route_hint": r.get("route_hint"),
        "kind": r.get("kind"),
    }


class Handler(BaseHTTPRequestHandler):
    def _client_ip(self) -> str:
        """取真实客户端 IP：优先 X-Forwarded-For 首跳（Nginx 反代场景）。"""
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

    def _send(self, code: int, payload: dict, headers: dict | None = None) -> None:
        out = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(out)

    def _rate_limited(self, dimension: str, key: str, d: rl.Decision) -> None:
        """超限：429 + Retry-After，并落观测（kind=rate_limit）。"""
        obs.record({"kind": "rate_limit", "dimension": dimension, "key": key,
                    "ok": False, "retry_after": d.retry_after, "backend": d.backend,
                    "detail": d.detail})
        self._send(429, {
            "error": {"message": "rate limited", "type": "rate_limit_error"},
            "rate_limit": d.to_dict(),
        }, {"Retry-After": str(max(1, int(d.retry_after)))})

    def do_POST(self):
        ip = self._client_ip()
        d = LIMITER.check("ip", ip)
        if not d.ok:
            self._rate_limited("ip", ip, d)
            return
        d = LIMITER.check("global", "gateway")
        if not d.ok:
            self._rate_limited("global", "gateway", d)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            messages = body.get("messages", [])
            task = messages[-1].get("content", "") if messages else ""
            model = body.get("model", "judge")
            lease = None
            if model.startswith("agent:"):
                agent = model.split(":", 1)[1]
                d, lease = LIMITER.acquire("agent", agent)
                if not d.ok:
                    self._rate_limited("agent", agent, d)
                    return
                lease.start_renewer()  # 长任务租约自动续期，进程退出由租约过期回收
            try:
                r, cached = _cached(model, task)
                if cached:
                    obs.record({"kind": "cache", "model": model, "ok": True})
            finally:
                if lease is not None:
                    lease.release()
            payload = to_openai_compat(r)
            payload["cached"] = cached
            headers = {"X-RateLimit-Remaining": str(max(0, int(d.remaining)))}
        except Exception as e:
            payload = {"error": {"message": f"{type(e).__name__}: {e}"}, "choices": []}
            headers = {}
        self._send(200, payload, headers)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"gateway 起在 http://{HOST}:{PORT}（Ctrl+C 停）")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

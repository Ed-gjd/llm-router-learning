"""lib/llm_client.py — 统一 OpenAI 兼容调用层 + 模型表加载（课1.1）

厂商差异（base_url / key / 模型档位 / 价格）全部下沉到 config/*.yaml，
这里只有通用逻辑：三家都 OpenAI 兼容，一个 OpenAI 客户端 + 每 provider 的 base_url/key。

统一返回结构（观测字段在 1.1 定，成本在 1.3 加）:
    provider / requested_model / used_model / tokens_in / tokens_out /
    latency_ms / ok / error / reply
"""
import os
import time
from pathlib import Path

from openai import OpenAI

from lib import obs
from lib import ratelimit as rl

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_yaml(name: str):
    import yaml
    raw = (CONFIG_DIR / name).read_text(encoding="utf-8")
    raw = os.path.expandvars(raw)  # 支持 ${ENV} 引用
    return yaml.safe_load(raw)


def load_providers() -> dict:
    """providers.yaml → {provider名: {base_url, key_env, enabled}}"""
    return load_yaml("providers.yaml")


def load_models() -> list:
    """models.yaml → [ {name, provider, tier, context, 价格, fallback} ]"""
    return load_yaml("models.yaml")


def _price_of(model_name: str) -> tuple[float, float]:
    """从模型表取单价（元/百万 token）。模型表没登记按 0 计。"""
    for m in load_models():
        if m["name"] == model_name:
            return m["price_in_per_mtok"], m["price_out_per_mtok"]
    return 0.0, 0.0


def _throttle(provider_name: str, provider: dict) -> dict:
    """RPM 限速（分布式，课4.x）：多实例共享配额；无 Redis 自动降级本地。"""
    d = rl.limiter.provider_throttle(provider_name, provider, wait=True)
    if d is None:
        return {}
    return {"rate_limit_allowed": d.ok, "rate_limit_retry_after": d.retry_after,
            "rate_limit_backend": d.backend}


def build_client(provider: dict) -> OpenAI:
    key = os.getenv(provider["key_env"])
    if not key:
        raise RuntimeError(f"{provider['name']}: 缺 key（环境变量 {provider['key_env']} 未设置）")
    return OpenAI(api_key=key, base_url=provider["base_url"], timeout=120)  # 推理模型思考久，超时放宽


def chat(provider_name: str, model: str, messages: list, route_hint: str | None = None, **kwargs) -> dict:
    """统一调用入口：传 provider 名 + 模型名，返回统一结构并落观测。

    route_hint：本路由决策的线索（manual:xxx / fallback:xxx / judge:xxx / round-robin#n），
    观测字段里记录，便于阶段3 复盘每次路由为什么这么走。
    """
    providers = load_providers()
    if provider_name not in providers:
        raise KeyError(f"未知 provider: {provider_name}（config/providers.yaml 里没有）")
    provider = providers[provider_name]
    if not provider.get("enabled", True):
        raise RuntimeError(f"{provider_name}: config 中未启用（缺 key 的登记槽位）")

    rate_limit = _throttle(provider_name, provider)  # RPM 限速（如 Kimi RPM=3，分布式）

    from lib import budget
    budget.check_budget()  # 预算护栏（课5.2）：ROUTER_BUDGET 超限拒绝

    t0 = time.time()
    try:
        resp = build_client(provider).chat.completions.create(model=model, messages=messages, **kwargs)
        tokens_in = resp.usage.prompt_tokens if resp.usage else 0
        tokens_out = resp.usage.completion_tokens if resp.usage else 0
        price_in, price_out = _price_of(model)
        cost = (tokens_in / 1e6) * price_in + (tokens_out / 1e6) * price_out
        result = {
            "provider": provider_name,
            "requested_model": model,
            "used_model": resp.model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": round(cost, 6),
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "ok": True,
            "route_hint": route_hint,
            "reply": resp.choices[0].message.content,
        }
        result.update(rate_limit)
    except Exception as e:
        result = {
            "provider": provider_name,
            "requested_model": model,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "ok": False,
            "route_hint": route_hint,
            "error": f"{type(e).__name__}: {e}",
        }
        result.update(rate_limit)
    obs.record(result)
    return result

#!/usr/bin/env python3
"""router_v1.py — 模型路由 v1（课1.2）

路由动作:
  --strategy manual       手动指定 --provider / --model
  --strategy round-robin  在启用模型间轮询（--times N 看轮转）
  --strategy fallback     沿 --model 的降级链走，主模型失败自动回退
  --strategy judge        便宜模型判任务难度(flash/plus/max) → 选档 → 降级链兜底
  --inject-failure        演练：主模型假装失败，验证降级链（不破坏真实 key）

用法:
  python3 router_v1.py --strategy manual --provider qwen --model qwen-plus --task "设计一个高可用架构方案"
  python3 router_v1.py --strategy fallback --model deepseek-chat --task "写首诗" --inject-failure
  python3 router_v1.py --strategy judge --task "推导一个复杂算法的复杂度"
"""
import argparse

from lib import llm_client as lc

MODELS = lc.load_models()
PROVIDERS = lc.load_providers()


def find_model(name: str):
    return next((m for m in MODELS if m["name"] == name), None)


def enabled_models() -> list:
    return [m for m in MODELS if PROVIDERS.get(m["provider"], {}).get("enabled", True)]


def call(m: dict, task: str, route_hint: str, inject: bool = False) -> dict:
    """调用一个模型；inject 时假装失败。"""
    if inject:
        print(f"  [注入] {m['name']} 假装不可用 → 走降级")
        return {"ok": False, "provider": m["provider"], "requested_model": m["name"],
                "error": "injected(演练)", "route_hint": route_hint}
    return lc.chat(m["provider"], m["name"],
                   [{"role": "user", "content": task}], route_hint=route_hint)


def route_fallback(model_name: str, task: str, inject: bool = False) -> dict:
    """沿模型降级链调用，主模型失败自动回退。"""
    m = find_model(model_name)
    if not m:
        raise SystemExit(f"模型表里没有 {model_name}")
    chain = [m] + [find_model(x) for x in m["fallback"] if find_model(x)]
    print(f"降级链: {' → '.join(x['name'] for x in chain)}")
    last = {}
    for idx, mm in enumerate(chain):
        # 注入只发生在"演练模式且主模型（链首）"，后续模型要真实接管
        r = call(mm, task, f"fallback:{model_name}", inject=inject and (idx == 0))
        if r.get("ok"):
            print(f"  实际使用: {mm['provider']}/{mm['name']}  reply={r.get('reply')!r}")
            return r
        last = r
    print(f"  降级链耗尽，最后一次失败: {last.get('error')}")
    return last


def route_round_robin(task: str, times: int) -> None:
    pool = enabled_models()
    print(f"轮询池: {[m['name'] for m in pool]}")
    for i in range(times):
        m = pool[i % len(pool)]
        r = call(m, task, f"round-robin#{i}")
        print(f"  [{i}] {m['name']} ok={r.get('ok')} reply={r.get('reply')!r}")


def route_judge(task: str) -> dict:
    """便宜模型当裁判：判任务难度档位 → 选档 → 降级链兜底。

    JSON 约束（结构化输出）：裁判返回 {"tier": "flash|plus|max"}，
    用 response_format=json_object 强制可解析，不依赖自然语言猜测。
    """
    judge_model = "deepseek-v4-flash"  # 裁判用便宜档（真实模型名）
    prompt = (
        '你是路由裁判。判断下面任务的难度档位，只输出 JSON，格式: '
        '{"tier": "flash" 或 "plus" 或 "max"}\n'
        "规则: 日常问答/翻译/摘要=flash; 需要推理/写代码/分析=plus; "
        "高难数学/系统设计/长文档=max。\n任务: " + task
    )
    r = lc.chat("deepseek", judge_model, [{"role": "user", "content": prompt}],
                max_tokens=16, response_format={"type": "json_object"},
                route_hint="judge:难度判断(JSON)")
    import json as _json
    tier = "plus"
    try:
        tier = _json.loads(r["reply"])["tier"]
    except Exception:
        tier = "plus"
    candidates = [m for m in enabled_models() if m["tier"] == tier] or enabled_models()
    target = candidates[0]
    print(f"[裁判] 难度档位={tier} → 目标模型={target['name']}（失败则走其降级链）")
    r = route_fallback(target["name"], task, inject=False)
    r["judged_tier"] = tier  # 供评测平台自评估命中率（毕业项目 C）
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description="模型路由 v1")
    ap.add_argument("--strategy", required=True,
                    choices=["manual", "round-robin", "fallback", "judge"])
    ap.add_argument("--provider", help="manual 用")
    ap.add_argument("--model", help="manual/fallback 用")
    ap.add_argument("--task", default="写一句介绍路由系统的开场白")
    ap.add_argument("--times", type=int, default=1, help="round-robin 轮数")
    ap.add_argument("--inject-failure", action="store_true", help="演练主模型故障")
    args = ap.parse_args()

    if args.strategy == "manual":
        if not args.provider or not args.model:
            raise SystemExit("manual 需要 --provider 和 --model")
        r = lc.chat(args.provider, args.model, [{"role": "user", "content": args.task}],
                    route_hint=f"manual:{args.provider}/{args.model}")
        print(f"manual 实际使用: {r.get('used_model')} reply={r.get('reply')!r} ok={r.get('ok')}")
    elif args.strategy == "round-robin":
        route_round_robin(args.task, args.times)
    elif args.strategy == "fallback":
        if not args.model:
            raise SystemExit("fallback 需要 --model")
        route_fallback(args.model, args.task, inject=args.inject_failure)
    elif args.strategy == "judge":
        route_judge(args.task)


if __name__ == "__main__":
    main()

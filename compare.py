#!/usr/bin/env python3
"""compare.py — 策略评测平台（毕业项目 C）

固定任务集 × 多条策略批量跑分：
    judge              裁判路由（自动选档）
    manual:deepseek/deepseek-chat   手动指定便宜档
    manual:qwen/qwen-plus           手动指定均衡档
    agent:codex        agent 执行端
输出：每策略的成功/成本/延迟合计 + 裁判命中率（对 GROUND_TRUTH 难度档）。

用法:
    python3 compare.py
"""
from lib import llm_client as lc
import router_v1 as rv

# (任务, 人工标的难度档) —— 自评估 ground truth
TASKS = [
    ("翻译：hello world", "flash"),
    ("写一个快速排序", "plus"),
    ("推导二项分布期望", "plus"),
    ("设计一个分布式限流方案", "max"),
]
STRATEGIES = ["judge", "manual:deepseek/deepseek-v4-flash", "manual:qwen/qwen-plus", "agent:codex", "agent:dsh"]


def run(strategy: str, task: str) -> dict:
    if strategy == "judge":
        r = rv.route_judge(task)
        return {"ok": r.get("ok"), "cost": r.get("cost", 0), "lat": r.get("latency_ms", 0),
                "tier": r.get("judged_tier"), "used": f"{r.get('provider')}/{r.get('used_model')}"}
    if strategy.startswith("manual:"):
        prov, model = strategy.split(":", 1)[1].split("/")
        r = lc.chat(prov, model, [{"role": "user", "content": task}], route_hint=f"manual:{prov}/{model}")
        return {"ok": r.get("ok"), "cost": r.get("cost", 0), "lat": r.get("latency_ms", 0),
                "tier": None, "used": f"{prov}/{model}"}
    if strategy.startswith("agent:"):
        from lib import agent_client as ac
        r = ac.run_agent(strategy.split(":", 1)[1], f"任务：{task}")
        ac.record_agent(r)
        return {"ok": r.get("ok"), "cost": 0.0, "lat": r.get("latency_ms", 0),
                "tier": None, "used": f"agent/{r.get('agent')}"}
    raise ValueError(strategy)


def main() -> None:
    print("=== 策略评测平台 ===")
    judge_hits = judge_total = 0
    for strat in STRATEGIES:
        ok = cost = lat = n = 0
        for task, truth in TASKS:
            r = run(strat, task)
            n += 1
            ok += 1 if r["ok"] else 0
            cost += r["cost"]; lat += r["lat"]
            if strat == "judge":
                judge_total += 1
                judge_hits += 1 if r["tier"] == truth else 0
        print(f"  {strat:<34} 成功{ok}/{n}  总成本{round(cost,5)}元  均延迟{round(lat/n)}ms")
    print(f"\n裁判命中率（判对难度档）：{judge_hits}/{judge_total} = "
          f"{round(judge_hits/judge_total*100) if judge_total else 0}%")


if __name__ == "__main__":
    main()

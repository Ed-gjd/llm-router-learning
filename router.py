#!/usr/bin/env python3
"""router.py — 一句话分发入口（毕业项目 B）

给一句任务，自动判断：
    - 纯问答 → 模型 API（router_v1 裁判路由，便宜）
    - 动手任务 → agent（claude/codex 等注册表 CLI）
返回：结果 + 成本 + 决策原因（route_hint）。

用法:
    python3 router.py "用一句话介绍路由"
    python3 router.py "写一个快速排序脚本"
"""
import sys

from agent_router import classify
from lib import agent_client as ac


def dispatch(task: str, agent: str | None = None) -> dict:
    if agent or classify(task) == "agent":
        r = ac.run_agent(agent or "claude", f"任务：{task}")
        ac.record_agent(r)
        r["kind"] = "agent"
        return r
    import router_v1 as rv
    r = rv.route_judge(task)
    r["kind"] = "llm"
    return r


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    task = " ".join(sys.argv[1:])
    r = dispatch(task)
    src = r.get("provider") or r.get("agent")
    print(f"[结果] kind={r.get('kind')}  执行端={src}  模型={r.get('used_model')}  "
          f"成本={r.get('cost', 0)}元  耗时={r.get('latency_ms')}ms")
    print(f"[原因] {r.get('route_hint')}")
    print()
    print(r.get("text") or r.get("reply") or r.get("error"))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""agent_router.py — 任务分派：模型 API vs claude/codex agent（课2.2）

一个入口判断"这任务该走模型 API 还是 agent"，自动分派，统一结果 + 观测。
- 分类：关键词规则命中→agent；否则→模型 API（走 router_v1 裁判选档）
- 运行时切换：--agent claude|codex 强制指定执行端；--model 实时换模型（课0.2 能力）

用法:
  python3 agent_router.py --task "用一句话介绍路由"
  python3 agent_router.py --task "在 tasks/ 下写一个 hello.py"
  python3 agent_router.py --task "写个排序函数" --agent codex --model deepseek-chat
"""
import argparse
import json

from lib import agent_client as ac


# 关键词规则：命中即认为需要动手（写代码/改文件）→ 交给 agent
AGENT_KEYWORDS = ["写", "创建", "修改", "重构", "实现", "修复", "文件", "代码", "脚本", "项目"]


def classify(task: str) -> str:
    """规则分类：返回 'agent' 或 'llm'。"""
    return "agent" if any(k in task for k in AGENT_KEYWORDS) else "llm"


def route_llm(task: str) -> dict:
    """走模型 API：复用 router_v1 的裁判路由（便宜模型判难度→选档→降级链）。"""
    import router_v1 as rv
    r = rv.route_judge(task)
    r["kind"] = "llm"
    return r


def route_agent(task: str, agent: str, model: str | None, cwd: str | None = None) -> dict:
    """走 agent：把任务拼进 prompt 交给注册表里任意 coding CLI 无头执行。"""
    prompt = f"任务：{task}\n请直接完成，不要询问。"
    r = ac.run_agent(agent, prompt, model=model, cwd=cwd)
    ac.record_agent(r)
    r["kind"] = "agent"
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description="任务分派：模型 API vs agent")
    ap.add_argument("--task", required=True, help="要完成的任务")
    ap.add_argument("--agent", choices=list(ac.load_agents().keys()), help="强制走指定 agent")
    ap.add_argument("--model", help="运行时换模型（agent 路径用）")
    args = ap.parse_args()

    is_agent = bool(args.agent) or classify(args.task) == "agent"
    dest = args.agent or ("claude" if is_agent else "llm")
    print(f"[分派] 任务: {args.task!r} → 执行端: {dest}"
          + (f"  agent={args.agent} model={args.model}" if args.agent else ""))

    result = route_agent(args.task, dest, args.model) if is_agent else route_llm(args.task)

    print(f"[结果] kind={result.get('kind')} ok={result.get('ok')}")
    if result.get("kind") == "agent":
        print(f"  执行端={result.get('agent')} 实际驱动={result.get('used_model')}")
        print(f"  输出: {(result.get('text') or '')[:200]}")
    else:
        print(f"  实际使用={result.get('provider')}/{result.get('used_model')} "
              f"cost={result.get('cost')} reply={(result.get('reply') or '')[:100]!r}")


if __name__ == "__main__":
    main()

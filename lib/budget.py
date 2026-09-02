#!/usr/bin/env python3
"""lib/budget.py — 成本预算护栏（课5.2）

环境变量 ROUTER_BUDGET 设置总额（元），router.jsonl 累计成本超过即拒绝新调用。
0（默认）= 不设限。llm_client.chat 每次调用前检查。
"""
import json
import os
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "router.jsonl"
BUDGET = float(os.getenv("ROUTER_BUDGET") or 0)


def spent() -> float:
    """从观测日志累计已花费（元）。"""
    if not DATA_FILE.exists():
        return 0.0
    total = 0.0
    for line in DATA_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += json.loads(line).get("cost") or 0
    return total


def check_budget() -> None:
    """超预算直接抛错，拒绝本次调用。"""
    if BUDGET <= 0:
        return
    s = spent()
    if s >= BUDGET:
        raise RuntimeError(
            f"预算超限：已花 {s:.4f} 元 ≥ 预算 {BUDGET} 元，拒绝调用"
            f"（提高 ROUTER_BUDGET，或备份后清空 data/router.jsonl 重计）")

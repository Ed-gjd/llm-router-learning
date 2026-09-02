"""lib/obs.py — 统一观测字段 + 落盘（阶段1 起统一，模型路由与 agent 路由共用）

统一字段约定（坑 #10：kind 从 1.1 就固定，否则阶段3 对不齐）:
    ts / kind(llm|agent) / provider / requested_model / used_model /
    tokens_in / tokens_out / cost / latency_ms / ok / error / route_hint
"""
import json
import time
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "router.jsonl"


def record(fields: dict) -> None:
    """追加一条观测记录到 data/router.jsonl。"""
    fields = dict(fields)
    fields.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    fields.setdefault("kind", "llm")
    with DATA_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    print(f"[obs] 已写 {DATA_FILE} 1 条")

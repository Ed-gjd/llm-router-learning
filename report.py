#!/usr/bin/env python3
"""report.py — 观测报告（毕业项目 A）

读 data/router.jsonl 全链路观测，回答：
    哪家最便宜（均成本/次）、哪条降级链触发过、成功率/延迟分布。

用法:
    python3 report.py
"""
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    path = Path(__file__).resolve().parent / "data" / "router.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("观测日志为空，先跑点任务再 report")
        return

    by_src = defaultdict(list)
    for r in rows:
        by_src[(r.get("kind"), r.get("provider") or r.get("agent"))].append(r)

    total_cost = sum(x.get("cost") or 0 for x in rows)
    print("=== 观测报告（全链路）===")
    n_all = ok_all = 0
    for (kind, src), rs in sorted(by_src.items()):
        n = len(rs); ok = sum(1 for x in rs if x.get("ok"))
        cost = sum(x.get("cost") or 0 for x in rs)
        avg = sum(x.get("latency_ms") or 0 for x in rs) / n
        share = cost / total_cost * 100 if total_cost else 0
        n_all += n; ok_all += ok
        print(f"  {kind:<6} {src:<10} {n:>3}次  成本{round(cost,5)}元({round(share,1)}%)  "
              f"均延迟{round(avg)}ms  成功率{round(ok/n*100)}%")

    fb = [r for r in rows if "fallback" in str(r.get("route_hint"))]
    print(f"\n降级链触发 {len(fb)} 次：{sorted(set(str(r.get('route_hint')) for r in fb))}")
    # 最便宜端（均成本/次最小）
    cheapest = min(by_src.items(), key=lambda kv: sum(x.get("cost", 0) for x in kv[1]) / len(kv[1]))
    print(f"最便宜端(均成本/次)：{cheapest[0][1]}")
    print(f"\n总计 {n_all} 次，总成本 {round(sum(x.get('cost',0) for x in rows),5)} 元，"
          f"成功率 {round(ok_all/n_all*100)}%")


if __name__ == "__main__":
    main()

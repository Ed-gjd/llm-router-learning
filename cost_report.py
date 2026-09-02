#!/usr/bin/env python3
"""cost_report.py — 成本核算对比（课1.3）

跑一批任务经 router_v1 裁判路由，统计实际成本；再按"若全用 max 档模型"做假设估算，
输出对比与节省比例。价格表在 config/models.yaml（单价元/百万token）。

用法:
    python3 cost_report.py
"""
from lib import llm_client as lc
import router_v1 as rv

TASKS = [
    "翻译：hello world",
    "用一句话解释什么是TCP三次握手",
    "写一个快速排序的Python实现",
    "推导二项分布期望的证明",
    "设计一个分布式限流方案的要点",
]


def main() -> None:
    models = {m["name"]: m for m in lc.load_models()}
    max_name = next(m["name"] for m in models.values() if m["tier"] == "max")
    routed_total = 0.0
    allmax_total = 0.0

    for t in TASKS:
        r = rv.route_judge(t)  # 真实路由：裁判选档 → 调用
        actual = r.get("cost", 0.0)
        routed_total += actual
        # 假设估算：同样 token 数，价格换成 max 档
        mm = models[max_name]
        est = (r.get("tokens_in", 0) / 1e6) * mm["price_in_per_mtok"] \
            + (r.get("tokens_out", 0) / 1e6) * mm["price_out_per_mtok"]
        allmax_total += est
        used = f"{r.get('provider')}/{r.get('used_model')}"
        print(f"  任务: {t[:18]!r}  实际[{used}]={actual:.5f}元  若全用{max_name}={est:.5f}元")

    if allmax_total > 0:
        save = (1 - routed_total / allmax_total) * 100
        print(f"\n合计: 路由后={routed_total:.5f}元  全用{max_name}={allmax_total:.5f}元  节省 {save:.1f}%")


if __name__ == "__main__":
    main()

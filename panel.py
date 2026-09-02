#!/usr/bin/env python3
"""panel.py — Web 观测面板（课4.2）

读 data/router.jsonl，每次请求动态渲染 HTML 页面：
    总计统计 / 各端明细(次数/成本/延迟/成功率) / 降级链触发 / 最近路由。

用法:
    python3 panel.py     # 起在 http://127.0.0.1:8124
"""
import html
import json
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8124
DATA = Path(__file__).resolve().parent / "data" / "router.jsonl"


def load_rows() -> list:
    if not DATA.exists():
        return []
    return [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]


def render() -> str:
    rows = load_rows()
    by_src = defaultdict(list)
    for r in rows:
        by_src[(r.get("kind"), r.get("provider") or r.get("agent"))].append(r)

    lines = ["<!doctype html><html><head><meta charset='utf-8'>",
             "<title>router-lab 观测面板</title>",
             "<style>body{font-family:monospace;margin:20px}table{border-collapse:collapse}",
             "td,th{border:1px solid #ccc;padding:4px 10px;text-align:right}",
             "th{background:#eee}.ok{color:green}.bad{color:red}</style></head><body>"]
    lines.append(f"<h1>router-lab 观测面板</h1><p>共 {len(rows)} 条记录</p>")
    if rows:
        cost = sum(x.get("cost") or 0 for x in rows)
        ok = sum(1 for x in rows if x.get("ok"))
        lines.append(f"<p>总成本 <b>{round(cost,5)}</b> 元 · 成功率 "
                     f"<b>{round(ok/len(rows)*100)}%</b></p>")
    lines.append("<table><tr><th>类型</th><th>来源</th><th>次数</th><th>成本(元)</th>"
                 "<th>均延迟ms</th><th>成功率</th></tr>")
    for (kind, src), rs in sorted(by_src.items()):
        n = len(rs); ok = sum(1 for x in rs if x.get("ok"))
        cost = sum(x.get("cost") or 0 for x in rs)
        avg = sum(x.get("latency_ms") or 0 for x in rs) / n
        cls = "ok" if ok / n >= 0.9 else "bad"
        lines.append(f"<tr><td>{kind}</td><td>{html.escape(str(src))}</td><td>{n}</td>"
                     f"<td>{round(cost,5)}</td><td>{round(avg)}</td>"
                     f"<td class='{cls}'>{round(ok/n*100)}%</td></tr>")
    lines.append("</table>")
    fb = [r for r in rows if "fallback" in str(r.get("route_hint"))]
    lines.append(f"<h3>降级链触发 {len(fb)} 次</h3>")
    for h in sorted(set(str(r.get("route_hint")) for r in fb)):
        lines.append(f"<p>{html.escape(h)}</p>")
    lines.append("<h3>最近 15 条路由</h3><table><tr><th>时间</th><th>kind</th><th>来源</th>"
                 "<th>route_hint</th><th>耗时ms</th></tr>")
    for r in rows[-15:][::-1]:
        src = r.get("provider") or r.get("agent")
        lines.append(f"<tr><td>{r.get('ts','')}</td><td>{r.get('kind')}</td>"
                     f"<td>{html.escape(str(src))}</td><td>{html.escape(str(r.get('route_hint')))}</td>"
                     f"<td>{r.get('latency_ms')}</td></tr>")
    lines.append("</table></body></html>")
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"panel 起在 http://127.0.0.1:{PORT}（Ctrl+C 停）")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

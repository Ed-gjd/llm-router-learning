#!/usr/bin/env python3
"""check_secrets.py — 密钥安全审计（课5.3）

扫描 router-lab 下代码/配置文件，找硬编码的 sk- 密钥。
只认真实长 token（sk- 后 ≥16 位字母数字下划线），占位符（sk-xxx 等）不算。

用法:
    python3 check_secrets.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", "data", "build"}
SCAN_EXTS = {".py", ".yaml", ".yml", ".toml", ".md", ".sh", ".json", ".txt"}
KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")


def scan() -> list:
    hits = []
    for p in ROOT.rglob("*"):
        if p.is_dir() or any(s in p.parts for s in SKIP_DIRS):
            continue
        if p.suffix not in SCAN_EXTS:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in KEY_RE.finditer(text):
            val = m.group()
            hits.append((p, val[:6] + "***" + val[-2:]))
    return hits


if __name__ == "__main__":
    hits = scan()
    if hits:
        print(f"⚠️ 发现 {len(hits)} 处疑似硬编码密钥：")
        for p, v in hits:
            print(f"  {p.relative_to(ROOT)}: {v}")
        sys.exit(1)
    print("✅ 未发现硬编码密钥（代码/配置干净，key 全走环境变量/外部配置文件）")

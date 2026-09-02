#!/usr/bin/env python3
"""envcheck.py v1 — 五端可用性矩阵（阶段0 验收成品）

五端:
  模型 API:  deepseek / qwen / kimi   —— 真实调用（便宜，一次一条消息）
  agent CLI: claude / codex           —— 默认只查 binary+配置（零成本）；
                                        --cli-probe 时各真实调用一次

关键认知：claude 与 codex 当前实际都驱动 deepseek-v4-flash 系（请求名≠实际驱动模型），
矩阵里"实际驱动模型"来自配置，可与 API 端返回对比。

用法:
    python3 envcheck.py               # 五端矩阵
    python3 envcheck.py --cli-probe   # CLI 端真实调用
    python3 envcheck.py --list-models # 列 API 端模型菜单
"""
import argparse
import json
import os
import shutil
import tomllib
from pathlib import Path

from openai import OpenAI

HOME = Path.home()

API_PROVIDERS = [
    {
        "name": "deepseek",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
    },
    {
        "name": "qwen",
        "base_url": os.getenv("DASHSCOPE_BASE_URL", ""),
        "key_env": "DASHSCOPE_API_KEY",
        "model": "qwen-plus",
    },
    {
        "name": "kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "key_env": "MOONSHOT_API_KEY",
        "model": "kimi-k2.6",
    },
]


def probe_api(p: dict) -> dict:
    """模型 API 端：发一条消息。返回矩阵一行。"""
    key = os.getenv(p["key_env"])
    row = {
        "端": p["name"], "类型": "llm",
        "key": "有" if key else "缺 key 待补",
        "base_url": p["base_url"] or "(未配置)",
        "实际驱动模型": "-",
        "可用": False, "说明": "",
    }
    if not key or not p["base_url"]:
        row["说明"] = "跳过（缺 key 或 base_url）"
        return row
    try:
        resp = OpenAI(api_key=key, base_url=p["base_url"], timeout=30).chat.completions.create(
            model=p["model"],
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )
        row["实际驱动模型"] = resp.model
        row["可用"] = True
        row["说明"] = f"reply={resp.choices[0].message.content!r}"
    except Exception as e:
        row["说明"] = f"{type(e).__name__}: {e}"
    return row


def claude_driving_model() -> tuple[str, str]:
    """从 ~/.claude/settings.json 提取 claude 实际驱动模型与后端。"""
    settings_path = HOME / ".claude" / "settings.json"
    if not settings_path.exists():
        return "(无 settings.json)", os.getenv("ANTHROPIC_BASE_URL", "?")
    data = json.loads(settings_path.read_text())
    slots = data.get("env", {})
    # 四槽全映射到 deepseek-v4-flash[1m]；取任一槽即代表实际驱动
    driving = slots.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or slots.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or data.get("model", "?")
    backend = os.getenv("ANTHROPIC_BASE_URL", "?")
    return driving, backend


def codex_driving_model() -> tuple[str, str, str]:
    """从 ~/.codex/config.toml 提取 codex 实际驱动模型、供应商、key 状态。"""
    config_path = HOME / ".codex" / "config.toml"
    if not config_path.exists():
        return "(无 config.toml)", "?", "?"
    cfg = tomllib.loads(config_path.read_text())
    model = cfg.get("model", "?")
    provider = cfg.get("model_provider", "?")
    prov = cfg.get("model_providers", {}).get(provider, {})
    key_env = prov.get("env_key", "?")
    key_ok = "有" if (os.getenv(key_env) if key_env != "?" else False) else "缺 key"
    return model, f"{provider}@{prov.get('base_url', '?')}", key_ok


def probe_cli(name: str, do_call: bool) -> dict:
    """agent CLI 端：binary+配置；do_call 时真实调用一次。"""
    row = {
        "端": name, "类型": "agent",
        "key": "-", "base_url": "-",
        "实际驱动模型": "-", "可用": False, "说明": "",
    }
    binary = shutil.which(name)
    if not binary:
        row["说明"] = "binary 未找到"
        return row
    row["key"] = "binary 存在"
    if name == "claude":
        driving, backend = claude_driving_model()
        row["实际驱动模型"] = driving
        row["base_url"] = backend
    elif name == "codex":
        driving, prov, key_ok = codex_driving_model()
        row["实际驱动模型"] = driving
        row["base_url"] = prov
        row["key"] = key_ok
    row["可用"] = True
    if do_call:
        # 真实无头调用验证（各一次）
        import subprocess
        try:
            if name == "claude":
                r = subprocess.run(["claude", "-p", "hi", "--output-format", "json"],
                                   capture_output=True, text=True, timeout=120, env=os.environ)
                ok = r.returncode == 0 and '"result"' in r.stdout
            else:
                r = subprocess.run(["codex", "exec", "--json", "--skip-git-repo-check", "hi"],
                                   capture_output=True, text=True, timeout=120, env=os.environ)
                ok = r.returncode == 0 and '"agent_message"' in r.stdout
            row["说明"] = "真实调用成功" if ok else f"真实调用异常 rc={r.returncode}"
        except Exception as e:
            row["说明"] = f"真实调用异常: {type(e).__name__}: {e}"
    else:
        row["说明"] = "binary+配置就绪（--cli-probe 可真实调用）"
    return row


def list_models(p: dict) -> None:
    key = os.getenv(p["key_env"])
    if not key or not p["base_url"]:
        print(f"[{p['name']}] 缺 key 或 base_url，跳过列模型")
        return
    try:
        ids = sorted(m.id for m in OpenAI(api_key=key, base_url=p["base_url"], timeout=30).models.list().data)
        print(f"[{p['name']}] 可用模型({len(ids)}): {', '.join(ids[:15])}{' …' if len(ids) > 15 else ''}")
    except Exception as e:
        print(f"[{p['name']}] 列模型失败: {type(e).__name__}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="五端可用性矩阵（模型API ×3 + agent CLI ×2）")
    ap.add_argument("--cli-probe", action="store_true", help="CLI 端也真实调用一次")
    ap.add_argument("--list-models", action="store_true", help="列出各 API 端可用模型")
    args = ap.parse_args()

    if args.list_models:
        for p in API_PROVIDERS:
            list_models(p)
        return

    rows = [probe_api(p) for p in API_PROVIDERS]
    rows.append(probe_cli("claude", args.cli_probe))
    rows.append(probe_cli("codex", args.cli_probe))

    print("=== 五端可用性矩阵 ===")
    for r in rows:
        print(
            f"[{r['端']:<9}] {r['类型']:<5} key={r['key']:<6} "
            f"base_url={r['base_url']:<60} 驱动={r['实际驱动模型']}"
        )
        print(f"          可用={r['可用']}  {r['说明']}")


if __name__ == "__main__":
    main()

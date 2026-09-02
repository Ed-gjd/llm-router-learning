"""lib/agent_client.py — 多 coding CLI 无头封装层（课2.2 扩展）

注册表驱动：config/agents.yaml 定义每个 CLI 的命令/无头参数/解析器/模型旗标，
run_agent(name, prompt, ...) 统一调度，输出统一 AgentResult 并支持观测(kind=agent)。

claude/codex 也走注册表；run_claude/run_codex 保留为兼容薄壳。
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from lib import obs

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_agents() -> dict:
    import yaml
    raw = (CONFIG_DIR / "agents.yaml").read_text(encoding="utf-8")
    raw = os.path.expandvars(raw)
    return yaml.safe_load(raw) or {}


def record_agent(result: dict) -> None:
    """agent 结果落观测（kind=agent），与 llm 共用 data/router.jsonl。"""
    obs.record({"kind": "agent", **{k: result.get(k) for k in (
        "agent", "exit_code", "text", "requested_model", "used_model",
        "tokens_in", "tokens_out", "latency_ms", "ok", "error", "route_hint")}})


# ---------- 解析器：stdout → 统一字段 ----------

def _find_text(node):
    """递归找常见文本键（result/text/content/message.content/choices…）。"""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        for k in ("result", "text", "content"):
            if isinstance(node.get(k), str):
                return node[k]
        choices = node.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            m = choices[0].get("message", {})
            if isinstance(m.get("content"), str):
                return m["content"]
        msg = node.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"]
        for v in node.values():
            t = _find_text(v)
            if t:
                return t
    if isinstance(node, list):
        for v in node:
            t = _find_text(v)
            if t:
                return t
    return ""


def _json_result(r, name, model):
    """通用 JSON 解析：兼容对象（claude/gemini）和数组（qwen 的事件流）。"""
    data = None
    for chunk in re.split(r"\n", r.stdout):
        chunk = chunk.strip()
        if chunk.startswith(("{", "[")):
            try:
                data = json.loads(chunk)
                break
            except Exception:
                continue

    text, used, usage = "", None, {}
    if isinstance(data, list):
        # 事件流（qwen/kimi 系）：找 type=result 事件取文本/用量/模型
        result_ev = next((e for e in data if isinstance(e, dict) and e.get("type") == "result"), None)
        text = (result_ev or {}).get("result") or _find_text(data)
        usage = (result_ev or {}).get("usage", {}) or {}
        stats = (result_ev or {}).get("stats", {}) or {}
        if isinstance(stats.get("models"), dict) and stats["models"]:
            used = next(iter(stats["models"]))
        else:
            for e in data:
                if isinstance(e, dict) and isinstance(e.get("model"), str):
                    used = e["model"]
                    break
    elif isinstance(data, dict):
        text = _find_text(data)
        usage = data.get("usage", {}) or {}
        for key in ("used_model", "canonicalModel"):
            if isinstance(data.get(key), str):
                used = data[key]
                break
        if not used and isinstance(data.get("modelUsage"), dict) and data["modelUsage"]:
            used = next(iter(data["modelUsage"]))
    return {
        "text": text, "used_model": used or model or f"{name}:?",
        "tokens_in": usage.get("input_tokens"), "tokens_out": usage.get("output_tokens"),
        "ok": bool(text),
    }


def parse_json_stdout(r, name, model):
    return _json_result(r, name, model)


def parse_codex_ndjson(r, name, model):
    texts, usage = [], {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "item.completed":
            item = ev.get("item", {})
            if item.get("type") == "agent_message":
                texts.append(item.get("text", ""))
        elif t == "turn.completed":
            usage = ev.get("usage", {})
    return {
        "text": "\n".join(texts),
        "used_model": _codex_config_model(),
        "tokens_in": usage.get("input_tokens"), "tokens_out": usage.get("output_tokens"),
        "ok": bool(texts),
    }


def parse_text_stdout(r, name, model):
    text = r.stdout.strip()
    return {"text": text, "used_model": model or f"{name}:?",
            "tokens_in": None, "tokens_out": None, "ok": bool(text)}


def parse_cline_ndjson(r, name, model):
    """cline --json：NDJSON 事件流，取 run_result.text / agent_event text。"""
    texts = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "run_result" and ev.get("text"):
            texts.append(ev["text"])
        elif ev.get("type") == "agent_event":
            e = ev.get("event", {})
            if e.get("type") == "content_end" and e.get("text"):
                texts.append(e["text"])
    return {"text": "\n".join(texts), "used_model": model or f"{name}:?",
            "tokens_in": None, "tokens_out": None, "ok": bool(texts)}


def parse_ndjson_text(r, name, model):
    """NDJSON 事件流：收集 type=text 事件的 part.text（opencode 等）。"""
    texts = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "text":
            part = ev.get("part", {})
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
    return {"text": "".join(texts), "used_model": model or f"{name}:?",
            "tokens_in": None, "tokens_out": None, "ok": bool(texts)}


PARSERS = {
    "claude_json": parse_json_stdout,
    "qwen_json": parse_json_stdout,
    "gemini_json": parse_json_stdout,
    "opencode_json": parse_ndjson_text,
    "cline_json": parse_cline_ndjson,
    "codex_ndjson": parse_codex_ndjson,
    "hermes_text": parse_text_stdout,
    "text": parse_text_stdout,
}


def _codex_config_model() -> str:
    try:
        import tomllib
        cfg = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text())
        return cfg.get("model", "?")
    except Exception:
        return "?"


def run_agent(name: str, prompt: str, model: str | None = None, cwd: str | None = None,
              extra_env: dict | None = None, extra_args: list | None = None,
              timeout: int = 300, mode: str = "default") -> dict:
    """通用入口：读注册表 → 拼参数 → 跑 → 归一。

    mode: "default" 保守（默认权限提示/拒绝）; "auto" 自动（追加该 agent 的 permission_auto 旗标）。
    """
    agents = load_agents()
    if name not in agents:
        raise KeyError(f"agents.yaml 里没有 {name}，可用: {list(agents)}")
    cfg = agents[name]
    args = [cfg["cmd"]] + [a.replace("{prompt}", prompt) for a in cfg.get("args", [])]
    if cfg.get("cwd"):  # agents.yaml 可声明工作目录（如 dsh 要在 ~/deepseek-harness 跑）
        cwd = os.path.expanduser(cfg["cwd"])
    if model and cfg.get("model_flag"):
        args += [cfg["model_flag"], model]
    if mode == "auto" and cfg.get("permission_auto"):
        args += cfg["permission_auto"]
    if extra_args:
        args += extra_args
    env = os.environ.copy()
    for k, v in cfg.get("env", {}).items():  # 注册表里声明的后端环境变量
        env[k] = os.path.expandvars(v)
    if extra_env:
        env.update(extra_env)
    t0 = time.time()
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env=env, cwd=cwd or os.getcwd())
        p = PARSERS.get(cfg.get("parse", "claude_json"), parse_text_stdout)(r, name, model)
        return {"agent": name, "exit_code": r.returncode,
                "requested_model": model or f"default({name})",
                "latency_ms": round((time.time() - t0) * 1000, 1), **p}
    except Exception as e:
        return {"agent": name, "ok": False, "requested_model": model or f"default({name})",
                "error": f"{type(e).__name__}: {e}"}


# ---------- 兼容薄壳 ----------

def run_claude(prompt, model=None, extra_env=None, timeout=180, cwd=None):
    return run_agent("claude", prompt, model=model, cwd=cwd, extra_env=extra_env, timeout=timeout)


def run_codex(prompt, model=None, provider=None, cwd=None, timeout=300):
    extra = (["-c", f"model_provider={provider}"] if provider else None)
    return run_agent("codex", prompt, model=model, cwd=cwd, extra_args=extra, timeout=timeout)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenScope · AI 编程 Token 用量看板 — Hermes / Codex / Claude Code / zcode 等 AI 编程工具的 token 消耗统计 Web 平台

用法:
    python server.py            # 默认 127.0.0.1:8787
    python server.py --port 9000
    python server.py --no-browser

数据源:
    - 自动发现已安装工具的本地数据（见 parsers.py）:
      Hermes   %LOCALAPPDATA%\\hermes\\state.db + profiles/*
      Codex    ~/.codex/state_*.sqlite（threads 表 + rollout JSONL 明细）
      Claude   ~/.claude/projects/*/*.jsonl（assistant 消息 usage）
      zcode    ~/.zcode/cli/db/db.sqlite（model_usage 请求级明细）
    - 可在 Web 界面手动添加其他路径的数据
所有源只读访问，不修改任何数据。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import webbrowser
from collections import defaultdict
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import parsers

APP_NAME = "TokenScope"
VERSION = "0.3.0"
DEFAULT_PORT = 8787

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
WEB_DIST = os.path.join(BASE_DIR, "web", "dist")

# 展示用标签
SOURCE_LABELS = {
    "desktop": "桌面端", "cli": "终端", "tui": "终端", "vscode": "VS Code",
    "ide": "IDE", "web": "Web", "api": "API", "cron": "定时任务",
    "subagent": "子代理",
    "telegram": "Telegram", "discord": "Discord", "slack": "Slack",
    "whatsapp": "WhatsApp", "signal": "Signal", "matrix": "Matrix",
    "teams": "Teams", "email": "邮件", "imessage": "iMessage",
}
TASK_LABELS = {
    "": "主对话", "title_generation": "标题生成", "approval": "审批",
    "summary": "摘要", "compaction": "压缩", "compression": "压缩",
    "subagent": "子代理", "delegate": "子代理", "delegation": "子代理",
    "cron": "定时任务", "memory": "记忆", "title": "标题生成",
    "vision": "图像分析", "codex": "Codex 集成", "embedding": "向量嵌入",
}

# 示例定价（USD / 每百万 tokens）
EXAMPLE_PRICING = {
    "deepseek-v4-flash": {"input": 0.28, "output": 0.42, "cache_read": 0.028, "cache_write": 0.28},
    "deepseek-chat": {"input": 0.28, "output": 0.42, "cache_read": 0.028, "cache_write": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_read": 0.055, "cache_write": 0.55},
    "gpt-5.6-sol": {"input": 1.25, "output": 10.00, "cache_read": 0.125, "cache_write": 1.875},
    "gpt-5.2": {"input": 1.25, "output": 10.00, "cache_read": 0.125, "cache_write": 1.875},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cache_read": 1.25, "cache_write": 3.75},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-opus-4-1": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "qwen3.8-max": {"input": 0.56, "output": 1.76, "cache_read": 0.056, "cache_write": 0.56},
    "GLM-5.2": {"input": 0.60, "output": 1.80, "cache_read": 0.06, "cache_write": 0.60},
}

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def default_hermes_home():
    return parsers._hermes_home()


def load_config():
    cfg = {"sources": [], "pricing": {}}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
            cfg.setdefault("sources", [])
            cfg.setdefault("pricing", {})
        except Exception:
            pass
    # 兼容旧配置：无 type 的源默认 hermes
    for s in cfg["sources"]:
        s.setdefault("type", "hermes")
        s.setdefault("auto", False)
    # 自动发现的源总是并入（去重）
    known = {s.get("path") for s in cfg["sources"]}
    for s in parsers.discover():
        if s["path"] not in known:
            cfg["sources"].append({**s, "auto": True})
            known.add(s["path"])
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 统一记录层
# ---------------------------------------------------------------------------

def all_records(cfg):
    """所有数据源的统一会话记录"""
    out = []
    for src in cfg["sources"]:
        try:
            out.extend(parsers.parse_source(src))
        except Exception:
            continue
    return out


def filter_records(records, ts_from, ts_to, tool=None):
    out = []
    for r in records:
        st = r.get("started_at")
        if not st:
            continue
        if st < ts_from or st >= ts_to:
            continue
        if tool and r.get("tool") != tool:
            continue
        out.append(r)
    return out


def est_cost(model, pricing, inp, out, cr, cw):
    m = model or ""
    p = pricing.get(m)
    if p is None:
        # 大小写不敏感兜底（如 GLM-5.2 vs glm-5.2）
        for k, v in pricing.items():
            if k.lower() == m.lower():
                p = v
                break
    if not p:
        return None
    return (inp / 1e6) * p.get("input", 0) + (out / 1e6) * p.get("output", 0) \
        + (cr / 1e6) * p.get("cache_read", 0) + (cw / 1e6) * p.get("cache_write", 0)


def record_cost(r, pricing):
    return est_cost(r.get("model"), pricing, r.get("input", 0), r.get("output", 0),
                    r.get("cache_read", 0), r.get("cache_write", 0))


def rec_to_row(r, pricing):
    """统一记录 → 前端 SessionRow（蛇形字段）"""
    return {
        "id": r.get("id", ""),
        "title": r.get("title") or "(无标题)",
        "model": r.get("model") or "未知",
        "tool": r.get("tool", ""),
        "tool_label": parsers.TOOL_LABELS.get(r.get("tool", ""), r.get("tool", "")),
        "cwd": r.get("cwd") or "",
        "source": r.get("source") or "",
        "source_label": SOURCE_LABELS.get(r.get("source") or "", parsers.TOOL_LABELS.get(r.get("tool", ""), r.get("source") or "")),
        "message_count": r.get("message_count") or 0,
        "tool_call_count": 0,
        "api_call_count": r.get("api_calls") or 0,
        "input_tokens": r.get("input") or 0,
        "output_tokens": r.get("output") or 0,
        "cache_read_tokens": r.get("cache_read") or 0,
        "cache_write_tokens": r.get("cache_write") or 0,
        "reasoning_tokens": r.get("reasoning") or 0,
        "started_at": r.get("started_at"),
        "ended_at": r.get("ended_at"),
        "db_cost": 0,
        "cost": record_cost(r, pricing),
    }


def label_for(mapping, key):
    return mapping.get(key, key if key else "未知")


def parse_range(qs, default_days=None):
    ts_from = 0
    ts_to = float("inf")
    if qs.get("from"):
        try:
            ts_from = float(qs["from"][0])
        except ValueError:
            pass
    if qs.get("to"):
        try:
            ts_to = float(qs["to"][0])
        except ValueError:
            pass
    return ts_from, ts_to


# ---------------------------------------------------------------------------
# API 实现
# ---------------------------------------------------------------------------

def api_summary(cfg, qs):
    ts_from, ts_to = parse_range(qs)
    tool = (qs.get("tool") or [""])[0]
    recs = filter_records(all_records(cfg), ts_from, ts_to, tool)
    pricing = cfg.get("pricing", {})

    totals = {"sessions": 0, "input": 0, "output": 0, "cache_read": 0,
              "cache_write": 0, "reasoning": 0, "api_calls": 0,
              "priced_cost": 0.0, "unpriced": 0, "cost": None, "priced": False}
    by_model = defaultdict(lambda: {"model": "", "sessions": 0, "input": 0, "output": 0,
                                    "cache_read": 0, "cache_write": 0, "reasoning": 0,
                                    "api_calls": 0, "cost": None, "priced": False})
    by_tool = defaultdict(lambda: {"key": "", "label": "", "sessions": 0, "input": 0, "output": 0,
                                   "cache_read": 0, "api_calls": 0, "cost": None})
    by_source = defaultdict(lambda: {"key": "", "label": "", "sessions": 0, "input": 0, "output": 0,
                                     "cache_read": 0, "api_calls": 0, "cost": None})

    for r in recs:
        m = r.get("model") or "未知模型"
        tk = r.get("tool") or "unknown"
        sk = r.get("source") or "unknown"
        totals["sessions"] += 1
        totals["input"] += r.get("input") or 0
        totals["output"] += r.get("output") or 0
        totals["cache_read"] += r.get("cache_read") or 0
        totals["cache_write"] += r.get("cache_write") or 0
        totals["reasoning"] += r.get("reasoning") or 0
        totals["api_calls"] += r.get("api_calls") or 0
        c = record_cost(r, pricing)
        if c is not None:
            totals["priced_cost"] += c
            totals["priced"] = True
        else:
            totals["unpriced"] += 1

        bm = by_model[m]
        bm["model"] = m
        bm["sessions"] += 1
        bm["input"] += r.get("input") or 0
        bm["output"] += r.get("output") or 0
        bm["cache_read"] += r.get("cache_read") or 0
        bm["cache_write"] += r.get("cache_write") or 0
        bm["reasoning"] += r.get("reasoning") or 0
        bm["api_calls"] += r.get("api_calls") or 0
        if c is not None:
            bm["cost"] = (bm["cost"] or 0) + c
            bm["priced"] = True

        bt = by_tool[tk]
        bt["sessions"] += 1
        bt["input"] += r.get("input") or 0
        bt["output"] += r.get("output") or 0
        bt["cache_read"] += r.get("cache_read") or 0
        bt["api_calls"] += r.get("api_calls") or 0
        if c is not None:
            bt["cost"] = (bt["cost"] or 0) + c

        bs = by_source[sk]
        bs["sessions"] += 1
        bs["input"] += r.get("input") or 0
        bs["output"] += r.get("output") or 0
        bs["cache_read"] += r.get("cache_read") or 0
        bs["api_calls"] += r.get("api_calls") or 0
        if c is not None:
            bs["cost"] = (bs["cost"] or 0) + c

    totals["cost"] = round(totals["priced_cost"], 4) if totals["priced"] else None

    def label(key):
        return parsers.TOOL_LABELS.get(key, key)

    return {
        "totals": totals,
        "by_model": sorted((dict(v) for v in by_model.values()), key=lambda x: x["input"], reverse=True),
        "by_tool": sorted(({**dict(v), "key": k, "label": label(k)} for k, v in by_tool.items()),
                          key=lambda x: x["input"], reverse=True),
        "by_source": sorted(({**dict(v), "key": k, "label": SOURCE_LABELS.get(k, label(k))} for k, v in by_source.items()),
                            key=lambda x: x["input"], reverse=True),
        "by_task": _api_by_task(cfg, ts_from, ts_to, tool),
    }


def _api_by_task(cfg, ts_from, ts_to, tool):
    """任务维度（仅 Hermes 的 session_model_usage 有该粒度）"""
    if tool and tool != "hermes":
        return []
    by_task = defaultdict(lambda: {"key": "", "label": "", "api_calls": 0, "input": 0,
                                   "output": 0, "cache_read": 0, "sessions": 0})
    for src in cfg["sources"]:
        if src.get("type") != "hermes":
            continue
        p = src.get("path")
        if not p or not os.path.isfile(p):
            continue
        try:
            conn = sqlite3.connect(p)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT task, api_call_count, input_tokens, output_tokens,
                       cache_read_tokens, session_id
                FROM session_model_usage WHERE first_seen >= ? AND first_seen < ?""",
                (ts_from, ts_to)).fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        for r in rows:
            tk = r["task"] or ""
            bt = by_task[tk]
            bt["key"] = tk
            bt["label"] = label_for(TASK_LABELS, tk)
            bt["api_calls"] += r["api_call_count"] or 0
            bt["input"] += r["input_tokens"] or 0
            bt["output"] += r["output_tokens"] or 0
            bt["cache_read"] += r["cache_read_tokens"] or 0
            bt["sessions"] += 1
    return sorted((dict(v) for v in by_task.values()), key=lambda x: x["api_calls"], reverse=True)


def api_timeline(cfg, qs):
    ts_from, ts_to = parse_range(qs)
    tool = (qs.get("tool") or [""])[0]
    granularity = qs.get("granularity", ["day"])[0]
    if granularity not in ("day", "week", "month"):
        granularity = "day"
    recs = filter_records(all_records(cfg), ts_from, ts_to, tool)
    buckets = defaultdict(lambda: {"date": "", "input": 0, "output": 0, "cache_read": 0,
                                   "cache_write": 0, "reasoning": 0})
    for r in recs:
        t = datetime.fromtimestamp(r["started_at"])
        if granularity == "day":
            key = t.strftime("%Y-%m-%d")
        elif granularity == "week":
            iso = t.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
        else:
            key = t.strftime("%Y-%m")
        b = buckets[key]
        b["date"] = key
        b["input"] += r.get("input") or 0
        b["output"] += r.get("output") or 0
        b["cache_read"] += r.get("cache_read") or 0
        b["cache_write"] += r.get("cache_write") or 0
        b["reasoning"] += r.get("reasoning") or 0
    return {"granularity": granularity,
            "points": [dict(buckets[k]) for k in sorted(buckets.keys())]}


SORT_FIELDS = {
    "started_at": "started_at", "ended_at": "ended_at",
    "input_tokens": "input", "output_tokens": "output",
    "cache_read_tokens": "cache_read", "reasoning_tokens": "reasoning",
    "api_call_count": "api_calls", "message_count": "message_count",
    "model": "model", "title": "title",
}


def api_sessions(cfg, qs):
    ts_from, ts_to = parse_range(qs)
    tool = (qs.get("tool") or [""])[0]
    recs = filter_records(all_records(cfg), ts_from, ts_to, tool)
    pricing = cfg.get("pricing", {})

    q = (qs.get("q") or [""])[0].strip().lower()
    if q:
        recs = [r for r in recs
                if q in (r.get("title") or "").lower()
                or q in (r.get("id") or "").lower()
                or q in (r.get("model") or "").lower()
                or q in (r.get("tool") or "").lower()
                or q in (r.get("cwd") or "").lower()]
    model = (qs.get("model") or [""])[0].strip()
    if model:
        recs = [r for r in recs if (r.get("model") or "") == model]
    source = (qs.get("source") or [""])[0].strip()
    if source:
        recs = [r for r in recs if (r.get("source") or "") == source]

    total = len(recs)
    sort_col = (qs.get("sort") or ["started_at"])[0]
    order = "ASC" if (qs.get("order") or ["desc"])[0].lower() == "asc" else "DESC"
    field = SORT_FIELDS.get(sort_col, "started_at")

    def sort_key(r):
        v = r.get(field)
        if field == "model":
            v = v or ""
        return v if v is not None else 0
    recs.sort(key=sort_key, reverse=(order == "DESC"))

    page = max(1, int((qs.get("page") or ["1"])[0]))
    page_size = min(200, max(1, int((qs.get("page_size") or ["20"])[0])))
    offset = (page - 1) * page_size
    items = [rec_to_row(r, pricing) for r in recs[offset:offset + page_size]]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def api_session_detail(cfg, sid):
    pricing = cfg.get("pricing", {})
    for r in all_records(cfg):
        if r.get("id") == sid:
            row = rec_to_row(r, pricing)
            usage = []
            # Hermes 源有 session_model_usage 明细
            if r.get("tool") == "hermes":
                for src in cfg["sources"]:
                    if src.get("type") != "hermes":
                        continue
                    p = src.get("path")
                    if not p or not os.path.isfile(p):
                        continue
                    try:
                        conn = sqlite3.connect(p)
                        conn.row_factory = sqlite3.Row
                        rows = conn.execute("""
                            SELECT model, task, api_call_count, input_tokens, output_tokens,
                                   cache_read_tokens, cache_write_tokens, reasoning_tokens,
                                   first_seen, last_seen
                            FROM session_model_usage WHERE session_id = ?""", (sid,)).fetchall()
                        conn.close()
                    except sqlite3.Error:
                        continue
                    for u in rows:
                        uc = est_cost(u["model"], pricing, u["input_tokens"] or 0,
                                      u["output_tokens"] or 0, u["cache_read_tokens"] or 0,
                                      u["cache_write_tokens"] or 0)
                        usage.append({
                            "model": u["model"] or "未知",
                            "task": u["task"] or "",
                            "task_label": label_for(TASK_LABELS, u["task"] or ""),
                            "api_call_count": u["api_call_count"] or 0,
                            "input_tokens": u["input_tokens"] or 0,
                            "output_tokens": u["output_tokens"] or 0,
                            "cache_read_tokens": u["cache_read_tokens"] or 0,
                            "cache_write_tokens": u["cache_write_tokens"] or 0,
                            "reasoning_tokens": u["reasoning_tokens"] or 0,
                            "first_seen": u["first_seen"], "last_seen": u["last_seen"],
                            "cost": round(uc, 4) if uc is not None else None,
                        })
            usage.sort(key=lambda x: x["api_call_count"], reverse=True)
            return {"session": row, "usage": usage}
    return None


def api_sources_full(cfg):
    out = []
    for s in cfg["sources"]:
        p = s.get("path", "")
        t = s.get("type", "hermes")
        recs = parsers.parse_source(s)
        exists = os.path.isfile(p) if t != "claude" else os.path.isdir(p)
        meta = {
            "path": p, "name": s.get("name", t), "type": t,
            "type_label": parsers.TOOL_LABELS.get(t, t),
            "auto": s.get("auto", False), "exists": exists,
            "size": os.path.getsize(p) if exists and t != "claude" else 0,
            "modified_at": os.path.getmtime(p) if exists and t != "claude" else 0,
            "db_sessions": len(recs),
            "total_input": sum(r.get("input") or 0 for r in recs),
            "total_output": sum(r.get("output") or 0 for r in recs),
            "total_cache_read": sum(r.get("cache_read") or 0 for r in recs),
            "last_activity": max((r.get("started_at") or 0 for r in recs), default=0),
        }
        out.append(meta)
    out.sort(key=lambda x: (not x["exists"], x["type"], x["name"]))
    return {"sources": out, "hermes_home": default_hermes_home()}


def api_models(cfg, qs):
    ts_from, ts_to = parse_range(qs)
    tool = (qs.get("tool") or [""])[0]
    recs = filter_records(all_records(cfg), ts_from, ts_to, tool)
    agg = defaultdict(lambda: {"sessions": 0, "api_calls": 0, "input_tokens": 0})
    for r in recs:
        m = r.get("model") or "未知"
        a = agg[m]
        a["sessions"] += 1
        a["api_calls"] += r.get("api_calls") or 0
        a["input_tokens"] += r.get("input") or 0
    return {"models": [{"model": k, **v} for k, v in
                       sorted(agg.items(), key=lambda x: x[1]["input_tokens"], reverse=True)]}


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    cfg = None

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _route_api(self, path, qs):
        cfg = self.cfg
        if path == "/api/health":
            return self._send_json({"ok": True, "app": APP_NAME, "version": VERSION,
                                    "sources": len(cfg["sources"])})
        if path == "/api/config":
            return self._send_json({"app": APP_NAME, "version": VERSION,
                                    "hermes_home": default_hermes_home(),
                                    "config_path": CONFIG_PATH,
                                    "web_dist": WEB_DIST})
        if path == "/api/sources":
            return self._send_json(api_sources_full(cfg))
        if path == "/api/summary":
            return self._send_json(api_summary(cfg, qs))
        if path == "/api/timeline":
            return self._send_json(api_timeline(cfg, qs))
        if path == "/api/sessions":
            return self._send_json(api_sessions(cfg, qs))
        if path == "/api/models":
            return self._send_json(api_models(cfg, qs))
        if path == "/api/pricing":
            return self._send_json({"pricing": cfg.get("pricing", {})})
        m = __import__("re").match(r"^/api/session/(.+)$", path)
        if m:
            sid = unquote(m.group(1))
            detail = api_session_detail(cfg, sid)
            if detail is None:
                return self._send_json({"error": "会话不存在"}, 404)
            return self._send_json(detail)
        return self._send_json({"error": "not found"}, 404)

    def _route_api_post(self, path, body):
        cfg = self.cfg
        if path == "/api/sources":
            p = (body.get("path") or "").strip()
            name = (body.get("name") or "").strip()
            stype = (body.get("type") or "").strip() or "hermes"
            if stype not in parsers.TOOL_LABELS:
                stype = "hermes"
            if not p:
                return self._send_json({"error": "缺少 path"}, 400)
            p = os.path.abspath(os.path.expanduser(p))
            if stype == "claude":
                if not os.path.isdir(p):
                    return self._send_json({"error": f"找不到目录: {p}"}, 400)
            else:
                db_file = p if p.lower().endswith(".db") or p.lower().endswith(".sqlite") \
                    else os.path.join(p, "state.db")
                if not os.path.isfile(db_file):
                    return self._send_json({"error": f"找不到数据库文件: {db_file}"}, 400)
                p = db_file
            for s in cfg["sources"]:
                if os.path.normpath(s["path"]).lower() == os.path.normpath(p).lower():
                    return self._send_json({"error": "该数据源已存在"}, 400)
            cfg["sources"].append({"type": stype, "name": name or stype,
                                   "path": os.path.normpath(p), "auto": False})
            save_config(cfg)
            parsers.invalidate_cache()
            return self._send_json(api_sources_full(cfg))
        if path == "/api/pricing":
            pricing = body.get("pricing")
            if not isinstance(pricing, dict):
                return self._send_json({"error": "pricing 必须是对象"}, 400)
            clean = {}
            for k, v in pricing.items():
                if not isinstance(v, dict):
                    continue
                clean[str(k)] = {kk: (float(vv) if isinstance(vv, (int, float)) else 0.0)
                                 for kk, vv in v.items()
                                 if kk in ("input", "output", "cache_read", "cache_write")}
            cfg["pricing"] = clean
            save_config(cfg)
            return self._send_json({"pricing": clean})
        if path == "/api/pricing/example":
            cfg["pricing"] = EXAMPLE_PRICING
            save_config(cfg)
            return self._send_json({"pricing": EXAMPLE_PRICING})
        return self._send_json({"error": "not found"}, 404)

    def _route_api_delete(self, path, qs):
        cfg = self.cfg
        if path == "/api/sources":
            p = (qs.get("path") or [""])[0]
            if not p:
                return self._send_json({"error": "缺少 path"}, 400)
            norm = os.path.normpath(p).lower()
            kept = [s for s in cfg["sources"]
                    if os.path.normpath(s["path"]).lower() != norm or s.get("auto")]
            if len(kept) == len(cfg["sources"]):
                return self._send_json({"error": "未找到该数据源（自动发现的源不可移除）"}, 400)
            cfg["sources"] = kept
            save_config(cfg)
            parsers.invalidate_cache()
            return self._send_json(api_sources_full(cfg))
        return self._send_json({"error": "not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path.startswith("/api/"):
            return self._route_api(path, qs)
        self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_body()
        return self._route_api_post(parsed.path, body)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return self._route_api_delete(parsed.path, qs)

    def _serve_static(self, path):
        if not os.path.isdir(WEB_DIST):
            self._send_json({"error": f"前端未构建，请先 cd web && npm install && npm run build（{WEB_DIST} 不存在）"}, 500)
            return
        rel = path.lstrip("/")
        if not rel or rel.endswith("/") or "/" in rel and not os.path.isfile(os.path.join(WEB_DIST, rel)):
            rel = "index.html"
        target = os.path.normpath(os.path.join(WEB_DIST, rel))
        if not target.startswith(os.path.normpath(WEB_DIST)):
            rel = "index.html"
            target = os.path.join(WEB_DIST, rel)
        if not os.path.isfile(target):
            target = os.path.join(WEB_DIST, "index.html")
        try:
            with open(target, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".json": "application/json; charset=utf-8",
            ".woff2": "font/woff2",
            ".map": "application/json",
        }.get(os.path.splitext(target)[1].lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    cfg = load_config()
    Handler.cfg = cfg
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"  {APP_NAME} v{VERSION}")
    print(f"  Hermes 数据目录: {default_hermes_home()}")
    print(f"  数据源: {len(cfg['sources'])} 个（" + ", ".join(
        f"{parsers.TOOL_LABELS.get(s.get('type'), s.get('type'))}:{s.get('name')}" for s in cfg["sources"]) + "）")
    print(f"  打开: {url}")
    print("  Ctrl+C 退出")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()

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
import re
import sqlite3
import sys
import time
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import parsers

APP_NAME = "TokenScope"
VERSION = "0.4.0"
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
def _to_f(v):
    """字符串/数字 → float（model-pricing.json 里的价格为字符串）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _load_pricing_json():
    """从内置 pricing.json 加载模型定价（以用户提供的 model-pricing.json 为准）。
    返回 {modelId: {"input": float, "output": float, "cache_read": float, "cache_write": float}}"""
    pricing = {}
    path = os.path.join(BASE_DIR, "pricing.json")
    if not os.path.isfile(path):
        return pricing
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for m in data.get("models", []):
            key = m.get("modelId")
            if not key:
                continue
            pricing[key] = {
                "input": _to_f(m.get("inputCostPerMillion")),
                "output": _to_f(m.get("outputCostPerMillion")),
                "cache_read": _to_f(m.get("cacheReadCostPerMillion")),
                "cache_write": _to_f(m.get("cacheCreationCostPerMillion")),
            }
    except Exception:
        pass
    return pricing

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
        except Exception:
            pass
    # 定价：以内置 pricing.json 为准（56 模型），config.json 自定义覆盖
    pricing = _load_pricing_json()
    pricing.update(cfg.get("pricing", {}))
    cfg["pricing"] = pricing
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


def _resolve_db_path(p, stype):
    """把用户填的路径解析为实际数据库文件：
    - 直接是文件（.db/.sqlite/.sqlite3 结尾）→ 原样返回
    - 目录 → 按工具类型找：hermes 找 state.db；codex 找 state_*.sqlite（取版本最大）；
      zcode 找 cli/db/db.sqlite 或 db.sqlite；找不到返回 None"""
    if p.lower().endswith((".db", ".sqlite", ".sqlite3")):
        return p
    if not os.path.isdir(p):
        return None
    if stype == "codex":
        return parsers._latest_state_db(p)
    if stype == "zcode":
        for cand in ("cli/db/db.sqlite", "db.sqlite"):
            f = os.path.join(p, cand)
            if os.path.isfile(f):
                return f
        return None
    f = os.path.join(p, "state.db")
    return f if os.path.isfile(f) else None


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

def _normalize_cwd(cwd):
    r"""归一化工作目录：normpath + 折叠连续反斜杠（Claude 解码可能产生 D:\\\work）"""
    if not cwd:
        return ""
    c = os.path.normpath(cwd)
    while "\\\\" in c:
        c = c.replace("\\\\", "\\")
    return c


def _project_key(cwd):
    """工作目录归一化分组键（Windows 不区分大小写）"""
    c = _normalize_cwd(cwd)
    if not c:
        return "(未知项目)"
    return c.lower() if os.name == "nt" else c


def _totals_of(recs, pricing):
    """对记录集合汇总 totals（独立函数，供当前窗口与上一周期对比复用）"""
    t = {"sessions": 0, "input": 0, "output": 0, "cache_read": 0,
         "cache_write": 0, "reasoning": 0, "api_calls": 0,
         "priced_cost": 0.0, "unpriced": 0, "cost": None, "priced": False}
    for r in recs:
        t["sessions"] += 1
        t["input"] += r.get("input") or 0
        t["output"] += r.get("output") or 0
        t["cache_read"] += r.get("cache_read") or 0
        t["cache_write"] += r.get("cache_write") or 0
        t["reasoning"] += r.get("reasoning") or 0
        t["api_calls"] += r.get("api_calls") or 0
        c = record_cost(r, pricing)
        if c is not None:
            t["priced_cost"] += c
            t["priced"] = True
        else:
            t["unpriced"] += 1
    t["cost"] = round(t["priced_cost"], 4) if t["priced"] else None
    return t


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
    by_project = defaultdict(lambda: {"key": "", "sessions": 0, "input": 0, "output": 0,
                                      "cache_read": 0, "reasoning": 0, "api_calls": 0,
                                      "cost": None, "priced": False})

    for r in recs:
        m = r.get("model") or "未知模型"
        tk = r.get("tool") or "unknown"
        sk = r.get("source") or "unknown"
        c = record_cost(r, pricing)
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

        pkey = _project_key(r.get("cwd") or "")
        bp = by_project[pkey]
        bp["key"] = _normalize_cwd(r.get("cwd")) or "(未知项目)"
        bp["sessions"] += 1
        bp["input"] += r.get("input") or 0
        bp["output"] += r.get("output") or 0
        bp["cache_read"] += r.get("cache_read") or 0
        bp["reasoning"] += r.get("reasoning") or 0
        bp["api_calls"] += r.get("api_calls") or 0
        if c is not None:
            bp["cost"] = (bp["cost"] or 0) + c
            bp["priced"] = True

    totals = _totals_of(recs, pricing)

    # 上一周期对比（等长前移窗口；"全部"无边界时不计算）
    prev_totals = None
    if ts_from > 0 and ts_to != float("inf"):
        span = ts_to - ts_from
        prev_totals = _totals_of(
            filter_records(all_records(cfg), ts_from - span, ts_from, tool), pricing)

    def label(key):
        return parsers.TOOL_LABELS.get(key, key)

    return {
        "totals": totals,
        "prev_totals": prev_totals,
        "by_model": sorted((dict(v) for v in by_model.values()), key=lambda x: x["input"], reverse=True),
        "by_tool": sorted(({**dict(v), "key": k, "label": label(k)} for k, v in by_tool.items()),
                          key=lambda x: x["input"], reverse=True),
        "by_source": sorted(({**dict(v), "key": k, "label": SOURCE_LABELS.get(k, label(k))} for k, v in by_source.items()),
                            key=lambda x: x["input"], reverse=True),
        "by_project": sorted((dict(v) for v in by_project.values()), key=lambda x: x["input"], reverse=True),
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
    if granularity not in ("day", "week", "month", "hour"):
        granularity = "day"
    recs = filter_records(all_records(cfg), ts_from, ts_to, tool)
    buckets = defaultdict(lambda: {"date": "", "input": 0, "output": 0, "cache_read": 0,
                                   "cache_write": 0, "reasoning": 0})
    # 小时粒度：从当天 0 点起，每小时一桶，连续到当前小时（缺失时段补 0）
    if granularity == "hour" and ts_from > 0:
        day_start = datetime.fromtimestamp(ts_from).replace(hour=0, minute=0, second=0, microsecond=0)
        now = datetime.now()
        end = datetime.fromtimestamp(ts_to) if ts_to != float("inf") else now
        if end > now:
            end = now
        cur = day_start
        while cur <= end:
            key = cur.strftime("%Y-%m-%d %H:00")
            buckets[key] = {"date": key, "input": 0, "output": 0, "cache_read": 0,
                            "cache_write": 0, "reasoning": 0}
            cur += timedelta(hours=1)
    for r in recs:
        t = datetime.fromtimestamp(r["started_at"])
        if granularity == "hour":
            key = t.strftime("%Y-%m-%d %H:00")
            if key not in buckets:
                continue
        elif granularity == "day":
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


def api_requests(cfg, qs):
    """请求明细：各工具单次 LLM 请求（zcode/claude/codex），hermes 降级为会话×模型×任务聚合"""
    ts_from, ts_to = parse_range(qs)
    tool = (qs.get("tool") or [""])[0]
    model = (qs.get("model") or [""])[0].strip()
    pricing = cfg.get("pricing", {})

    reqs = []
    for src in cfg["sources"]:
        if tool and src.get("type") != tool:
            continue
        for r in parsers.extract_requests(src):
            st = r.get("started_at")
            if st:
                if st < ts_from or st >= ts_to:
                    continue
            elif ts_from > 0 or ts_to != float("inf"):
                continue  # 无时间戳（codex），有时间筛选时跳过
            if model and r.get("model") != model:
                continue
            r["cost"] = est_cost(r["model"], pricing, r["input"], r["output"],
                                 r["cache_read"], r["cache_write"])
            reqs.append(r)

    reqs.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
    total = len(reqs)
    totals = {
        "count": total,
        "input": sum(r["input"] for r in reqs),
        "output": sum(r["output"] for r in reqs),
        "cache_read": sum(r["cache_read"] for r in reqs),
        "cost": sum(r.get("cost") or 0 for r in reqs if r.get("cost") is not None),
    }
    page = max(1, int((qs.get("page") or ["1"])[0]))
    page_size = min(200, max(1, int((qs.get("page_size") or ["50"])[0])))
    offset = (page - 1) * page_size
    return {"total": total, "totals": totals, "page": page, "page_size": page_size,
            "items": reqs[offset:offset + page_size]}


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


def api_export_csv(cfg, qs):
    """导出当前筛选条件下的会话明细为 CSV（utf-8-sig，Excel 直接打开不乱码）"""
    import csv as csvmod
    import io
    ts_from, ts_to = parse_range(qs)
    tool = (qs.get("tool") or [""])[0]
    recs = filter_records(all_records(cfg), ts_from, ts_to, tool)
    q = (qs.get("q") or [""])[0].strip().lower()
    if q:
        recs = [r for r in recs
                if q in (r.get("title") or "").lower()
                or q in (r.get("id") or "").lower()
                or q in (r.get("model") or "").lower()
                or q in (r.get("cwd") or "").lower()]
    model = (qs.get("model") or [""])[0].strip()
    if model:
        recs = [r for r in recs if (r.get("model") or "") == model]
    source = (qs.get("source") or [""])[0].strip()
    if source:
        recs = [r for r in recs if (r.get("source") or "") == source]
    recs.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
    pricing = cfg.get("pricing", {})

    buf = io.StringIO()
    w = csvmod.writer(buf)
    w.writerow(["工具", "标题", "模型", "来源", "工作目录", "开始时间", "结束时间",
                "消息数", "API调用", "输入tokens", "输出tokens", "缓存读取tokens",
                "缓存写入tokens", "推理tokens", "估算成本USD"])
    for r in recs:
        c = record_cost(r, pricing)
        w.writerow([
            parsers.TOOL_LABELS.get(r.get("tool", ""), r.get("tool", "")),
            r.get("title") or "",
            r.get("model") or "",
            r.get("source") or "",
            r.get("cwd") or "",
            _fmt_ts(r.get("started_at")), _fmt_ts(r.get("ended_at")),
            r.get("message_count") or 0, r.get("api_calls") or 0,
            r.get("input") or 0, r.get("output") or 0,
            r.get("cache_read") or 0, r.get("cache_write") or 0,
            r.get("reasoning") or 0,
            "" if c is None else round(c, 6),
        ])
    return buf.getvalue()


def _fmt_ts(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dur(start, end):
    if not start:
        return "—"
    secs = max(0, int((end or time.time()) - start))
    if secs < 60:
        return f"{secs} 秒"
    if secs < 3600:
        return f"{secs // 60} 分 {secs % 60} 秒"
    return f"{secs // 3600} 小时 {(secs % 3600) // 60} 分"


def api_export_session_md(cfg, sid):
    """导出单个会话为 Markdown 文档（元信息 + token 汇总 + 完整对话正文）"""
    rec = None
    for r in all_records(cfg):
        if r.get("id") == sid:
            rec = r
            break
    if rec is None:
        return None
    src = None
    for s in cfg["sources"]:
        if s.get("type") == rec.get("tool"):
            src = s
            break
    msgs = parsers.extract_session_messages(src, sid) if src else []
    return _render_session_md(rec, msgs)


def _render_session_md(rec, msgs):
    tool_label = parsers.TOOL_LABELS.get(rec.get("tool"), rec.get("tool") or "")
    src_label = SOURCE_LABELS.get(rec.get("source") or "", rec.get("source") or "")
    lines = []
    lines.append(f"# {rec.get('title') or '(无标题)'}")
    lines.append("")
    lines.append(f"> **工具**：{tool_label}　**模型**：{rec.get('model') or '未知'}　**来源**：{src_label}")
    lines.append(f"> **会话 ID**：`{rec.get('id') or ''}`")
    lines.append(f"> **开始**：{_fmt_ts(rec.get('started_at'))}　**时长**：{_fmt_dur(rec.get('started_at'), rec.get('ended_at'))}")
    if rec.get("cwd"):
        lines.append(f"> **工作目录**：`{rec.get('cwd')}`")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 输入 Tokens | {rec.get('input') or 0:,} |")
    lines.append(f"| 输出 Tokens | {rec.get('output') or 0:,} |")
    lines.append(f"| 缓存读取 | {rec.get('cache_read') or 0:,} |")
    lines.append(f"| 缓存写入 | {rec.get('cache_write') or 0:,} |")
    lines.append(f"| 推理 Tokens | {rec.get('reasoning') or 0:,} |")
    lines.append(f"| API 调用 | {rec.get('api_calls') or 0:,} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 对话内容")
    lines.append("")
    if not msgs:
        lines.append("> 未提取到对话消息（该工具源可能未保存消息正文）。")
        lines.append("")
    role_title = {"user": "👤 用户", "assistant": "🤖 助手", "tool": "🔧 工具"}
    for m in msgs:
        role = m.get("role") or ""
        title = role_title.get(role, role)
        if role == "tool" and m.get("tool"):
            title = f"🔧 工具：{m['tool']}"
        lines.append(f"### {title}")
        lines.append("")
        if m.get("content"):
            lines.append(m["content"])
            lines.append("")
        if m.get("reasoning"):
            lines.append("<details>")
            lines.append("<summary>💭 思考过程</summary>")
            lines.append("")
            lines.append(m["reasoning"])
            lines.append("")
            lines.append("</details>")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
        if path == "/api/requests":
            return self._send_json(api_requests(cfg, qs))
        if path == "/api/models":
            return self._send_json(api_models(cfg, qs))
        if path == "/api/export.csv":
            content = api_export_csv(cfg, qs)
            body = content.encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="tokenscope-sessions.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/pricing":
            return self._send_json({"pricing": cfg.get("pricing", {})})
        m = re.match(r"^/api/session/(.+)/export\.md$", path)
        if m:
            sid = unquote(m.group(1))
            md = api_export_session_md(cfg, sid)
            if md is None:
                return self._send_json({"error": "会话不存在"}, 404)
            body = md.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="session-{sid}.md"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        m = re.match(r"^/api/session/(.+)$", path)
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
                db_file = _resolve_db_path(p, stype)
                if not db_file or not os.path.isfile(db_file):
                    hint = ("state_*.sqlite" if stype == "codex"
                            else "cli/db/db.sqlite" if stype == "zcode" else "state.db")
                    return self._send_json(
                        {"error": f"找不到数据库文件: 在 {p} 下未找到 {hint}"}, 400)
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
            base = _load_pricing_json()
            cfg["pricing"] = base
            save_config(cfg)
            return self._send_json({"pricing": base})
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

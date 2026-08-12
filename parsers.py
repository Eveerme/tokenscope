# -*- coding: utf-8 -*-
"""
parsers.py — 多工具会话/用量统一解析器

将 Hermes / Codex / Claude Code / zcode 的本地数据解析为统一的会话记录，
供 server.py 聚合统计。所有解析器只读，不修改任何源数据。

统一记录字段:
    tool         hermes | codex | claude | zcode
    id           会话 ID
    title        会话标题
    model        模型名（空串表示未知）
    cwd          工作目录/项目路径
    source       来源标签（hermes: desktop/cli；codex: vscode/cli；claude/zcode: 工具名）
    started_at   开始时间（unix 秒）
    ended_at     结束时间（unix 秒，可能为 None）
    input / output / cache_read / cache_write / reasoning   token 数
    api_calls     API 调用次数
    message_count 消息数
"""

import glob
import json
import os
import re
import sqlite3
from datetime import datetime

TOOL_LABELS = {"hermes": "Hermes", "codex": "Codex", "claude": "Claude Code", "zcode": "zcode"}

# 解析结果缓存: {path: (key, records)}，key 为数据文件/目录的最新 mtime
_cache = {}


def _clean_win_path(p):
    """剥离 Windows 长路径前缀 \\?\\ 或 \\\\?\\"""
    if not p:
        return p
    for pre in ("\\\\?\\", "\\\\\\?\\"):
        if p.startswith(pre):
            return p[len(pre):]
    return p


def _ms_to_s(v):
    """毫秒或秒时间戳统一转 unix 秒（>1e12 视为毫秒）"""
    if not v:
        return None
    if v > 1e12:
        return v / 1000.0
    return float(v)


def _iso_to_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _record(tool, **kw):
    base = {
        "tool": tool, "id": "", "title": "(无标题)", "model": "", "cwd": "",
        "source": tool, "started_at": None, "ended_at": None,
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        "reasoning": 0, "api_calls": 0, "message_count": 0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 数据源自动发现
# ---------------------------------------------------------------------------

def _hermes_home():
    h = os.environ.get("HERMES_HOME")
    if h and os.path.isdir(h):
        return h
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            p = os.path.join(local, "hermes")
            if os.path.isdir(p):
                return p
        roaming = os.environ.get("APPDATA", "")
        if roaming:
            p = os.path.join(roaming, "hermes")
            if os.path.isdir(p):
                return p
    p = os.path.expanduser("~/.hermes")
    return p if os.path.isdir(p) else os.path.expanduser("~/.hermes")


def _latest_state_db(home):
    """codex 的 state_*.sqlite 取版本号最大的"""
    cands = glob.glob(os.path.join(home, "state_*.sqlite"))
    if not cands:
        return None

    def ver(p):
        m = re.search(r"state_(\d+)\.sqlite$", p)
        return int(m.group(1)) if m else 0
    return max(cands, key=ver)


def discover():
    """自动发现已安装工具的数据源: [{type, name, path}]"""
    out = []
    # --- Hermes: state.db（主实例 + profiles）---
    home = _hermes_home()
    main_db = os.path.join(home, "state.db")
    if os.path.isfile(main_db):
        out.append({"type": "hermes", "name": "default", "path": os.path.normpath(main_db)})
    prof_dir = os.path.join(home, "profiles")
    if os.path.isdir(prof_dir):
        for name in sorted(os.listdir(prof_dir)):
            db = os.path.join(prof_dir, name, "state.db")
            if os.path.isfile(db):
                out.append({"type": "hermes", "name": name, "path": os.path.normpath(db)})

    # --- Codex: state_*.sqlite ---
    codex_home = os.path.expanduser("~/.codex")
    if os.path.isdir(codex_home):
        db = _latest_state_db(codex_home)
        if db:
            out.append({"type": "codex", "name": "codex", "path": os.path.normpath(db)})

    # --- Claude Code: projects 目录 ---
    claude_projects = os.path.expanduser("~/.claude/projects")
    if os.path.isdir(claude_projects):
        out.append({"type": "claude", "name": "claude", "path": os.path.normpath(claude_projects)})

    # --- zcode: cli/db/db.sqlite ---
    zcode_db = os.path.expanduser("~/.zcode/cli/db/db.sqlite")
    if os.path.isfile(zcode_db):
        out.append({"type": "zcode", "name": "zcode", "path": os.path.normpath(zcode_db)})

    return out


# ---------------------------------------------------------------------------
# 缓存与入口
# ---------------------------------------------------------------------------

def _key_for(path, is_dir=False):
    if is_dir:
        mx = 0.0
        try:
            for root, _dirs, files in os.walk(path):
                for f in files:
                    if f.endswith(".jsonl"):
                        mx = max(mx, os.path.getmtime(os.path.join(root, f)))
        except OSError:
            pass
        return mx
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except OSError:
        return 0.0


def parse_source(src):
    """按数据源类型解析（带 mtime 缓存），返回统一会话记录列表"""
    t = src.get("type", "hermes")
    p = src.get("path", "")
    key = _key_for(p, is_dir=(t == "claude"))
    cached = _cache.get(p)
    if cached and cached[0] == key:
        return cached[1]
    if t == "codex":
        recs = parse_codex(p)
    elif t == "claude":
        recs = parse_claude(p)
    elif t == "zcode":
        recs = parse_zcode(p)
    else:
        recs = parse_hermes(p)
    _cache[p] = (key, recs)
    return recs


def invalidate_cache():
    _cache.clear()


# ---------------------------------------------------------------------------
# Hermes
# ---------------------------------------------------------------------------

def parse_hermes(db_path):
    recs = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT id, title, model, source, profile_name, cwd,
                   message_count, tool_call_count, api_call_count,
                   input_tokens, output_tokens, cache_read_tokens,
                   cache_write_tokens, reasoning_tokens, started_at, ended_at
            FROM sessions""").fetchall()
        for r in rows:
            recs.append(_record(
                "hermes", id=r["id"], title=r["title"] or "(无标题)",
                model=r["model"] or "", cwd=r["cwd"] or "",
                source=r["source"] or "desktop",
                started_at=r["started_at"], ended_at=r["ended_at"],
                input=r["input_tokens"] or 0, output=r["output_tokens"] or 0,
                cache_read=r["cache_read_tokens"] or 0,
                cache_write=r["cache_write_tokens"] or 0,
                reasoning=r["reasoning_tokens"] or 0,
                api_calls=r["api_call_count"] or 0,
                message_count=r["message_count"] or 0,
            ))
        conn.close()
    except sqlite3.Error:
        pass
    return recs


# ---------------------------------------------------------------------------
# Codex: state_*.sqlite threads 表 + rollout JSONL 明细
# ---------------------------------------------------------------------------

_codex_model = None


def _codex_source(raw):
    """codex 的 source 字段：子代理线程的 source 是 JSON 字符串，归为 subagent"""
    if not raw:
        return "cli"
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            if isinstance(d, dict) and "subagent" in d:
                return "subagent"
        except (json.JSONDecodeError, TypeError):
            pass
        return "subagent"
    return raw


def _codex_default_model():
    """从 ~/.codex/config.toml 读取 model 作为默认模型"""
    global _codex_model
    if _codex_model is not None:
        return _codex_model
    _codex_model = ""
    try:
        cfg = open(os.path.expanduser("~/.codex/config.toml"), encoding="utf-8").read()
        m = re.search(r'^\s*model\s*=\s*"([^"]+)"', cfg, re.MULTILINE)
        if m:
            _codex_model = m.group(1)
    except OSError:
        pass
    return _codex_model


def _codex_rollout_detail(path):
    """读 rollout JSONL，取最后一个 token_count 事件的累计用量 + 事件数 + 真实来源(originator)"""
    p = _clean_win_path(path)
    try:
        with open(p, encoding="utf-8") as fh:
            last = None
            count = 0
            originator = None
            for line in fh:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if d.get("type") == "session_meta":
                    originator = d.get("payload", {}).get("originator")
                elif d.get("type") == "event_msg":
                    pl = d.get("payload", {})
                    if pl.get("type") == "token_count":
                        u = pl.get("info", {}).get("total_token_usage")
                        if u:
                            last = u
                            count += 1
        if not last:
            # 0 消耗会话（无 token_count 事件）也要保留真实来源
            return {
                "input": 0, "output": 0, "cache_read": 0, "reasoning": 0,
                "calls": 0, "originator": originator,
            }
        return {
            "input": last.get("input_tokens") or 0,
            "output": last.get("output_tokens") or 0,
            "cache_read": last.get("cached_input_tokens") or 0,
            "reasoning": last.get("reasoning_output_tokens") or 0,
            "calls": count,
            "originator": originator,
        }
    except OSError:
        return None


def _codex_source_from_originator(raw):
    """Codex 的 source 字段把桌面/IDE 都记成 'vscode'，实际来源看 session_meta.originator：
    Codex Desktop -> desktop（桌面端）；Codex CLI -> cli（终端）"""
    if not raw:
        return None
    if "Desktop" in raw:
        return "desktop"
    if "CLI" in raw:
        return "cli"
    return None


def parse_codex(state_db, default_model=None):
    """解析 Codex 的 state_*.sqlite（threads 表）+ rollout JSONL 明细。

    default_model: 模型名兜底；不传则读 ~/.codex/config.toml（测试/离线环境可显式传入）"""
    recs = []
    if default_model is None:
        default_model = _codex_default_model()
    try:
        conn = sqlite3.connect(state_db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT id, rollout_path, created_at, updated_at, source,
                   model_provider, cwd, title, tokens_used
            FROM threads""").fetchall()
        conn.close()
        for r in rows:
            detail = _codex_rollout_detail(r["rollout_path"])
            if detail:
                inp = detail["input"]
                out = detail["output"]
                cr = detail["cache_read"]
                rsn = detail["reasoning"]
                calls = detail["calls"]
                src = _codex_source_from_originator(detail.get("originator")) or _codex_source(r["source"])
            else:
                # 降级：只有总量，按输入计入
                inp, out, cr, rsn, calls = (r["tokens_used"] or 0), 0, 0, 0, 1
                src = _codex_source(r["source"])
            recs.append(_record(
                "codex", id=r["id"],
                title=r["title"] or "(无标题)",
                model=default_model,
                cwd=_clean_win_path(r["cwd"] or ""),
                source=src,
                started_at=_ms_to_s(r["created_at"]),
                ended_at=_ms_to_s(r["updated_at"]),
                input=inp, output=out, cache_read=cr, reasoning=rsn,
                api_calls=calls, message_count=0,
            ))
    except sqlite3.Error:
        pass
    return recs


# ---------------------------------------------------------------------------
# Claude Code: projects/<encoded>/<session>.jsonl
# ---------------------------------------------------------------------------

def _decode_claude_project(name):
    """Claude 项目目录名解码。

    Claude 把路径中的特殊字符（:\\/ 等）逐个转义为 '-'：
      D:\\work\\AI\\AiAgent\\agent -> D--work-AI-AiAgent-agent
    解码启发式：
      - 盘符开头（如 D--work...）：第一个 '--' 还原为 ':\\'，其余 '-' -> '\\'
      - 非盘符（macOS/Linux）：'-' -> '/'
    原始路径含 '-' 的场景（编码为 -2d 等）无法完美还原，属边缘情况。
    """
    if re.match(r"^[A-Za-z]--", name):
        s = name.replace("--", ":\\", 1)
        return s.replace("-", "\\")
    return name.replace("-", "/")


def _claude_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                return (blk.get("text") or "").strip()
    return ""


def parse_claude(projects_dir):
    recs = []
    if not os.path.isdir(projects_dir):
        return recs
    for proj_name in sorted(os.listdir(projects_dir)):
        proj_path = os.path.join(projects_dir, proj_name)
        if not os.path.isdir(proj_path):
            continue
        cwd = _decode_claude_project(proj_name)
        for fname in os.listdir(proj_path):
            if not fname.endswith(".jsonl"):
                continue
            fpath = os.path.join(proj_path, fname)
            session_id = fname[:-6]
            title = ""
            model = ""
            started = ended = None
            inp = out = cr = cw = rsn = calls = msgs = 0
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        try:
                            d = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        t = d.get("type")
                        ts = _iso_to_ts(d.get("timestamp"))
                        if started is None and ts:
                            started = ts
                        if ts:
                            ended = ts
                        if t == "assistant":
                            msg = d.get("message") or {}
                            u = msg.get("usage") or {}
                            inp += u.get("input_tokens") or 0
                            out += u.get("output_tokens") or 0
                            cr += u.get("cache_read_input_tokens") or 0
                            cw += u.get("cache_creation_input_tokens") or 0
                            calls += 1
                            if not model and msg.get("model"):
                                model = msg["model"]
                        elif t == "user" and not title:
                            txt = _claude_text(d.get("message", {}).get("content"))
                            if txt:
                                title = txt[:60]
                            else:
                                title = "(无标题)"
                        msgs += 1
            except OSError:
                continue
            recs.append(_record(
                "claude", id=session_id, title=title or "(无标题)",
                model=model, cwd=cwd, source="claude",
                started_at=started, ended_at=ended,
                input=inp, output=out, cache_read=cr, cache_write=cw,
                reasoning=rsn, api_calls=calls, message_count=msgs,
            ))
    return recs


# ---------------------------------------------------------------------------
# zcode: cli/db/db.sqlite（model_usage 请求级明细）
# ---------------------------------------------------------------------------

def parse_zcode(db_path):
    recs = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        # 会话基础信息
        sess_rows = conn.execute("""
            SELECT id, directory, title, time_created, time_updated
            FROM session""").fetchall()
        sess = {}
        for r in sess_rows:
            sess[r["id"]] = {
                "cwd": r["directory"] or "",
                "title": r["title"] or "",
                "created": r["time_created"],
                "updated": r["time_updated"],
            }
        # 用量聚合（请求级）
        usage_rows = conn.execute("""
            SELECT session_id, COUNT(*) AS calls,
                   SUM(input_tokens) AS inp, SUM(output_tokens) AS out,
                   SUM(reasoning_tokens) AS rsn,
                   SUM(cache_creation_input_tokens) AS cw,
                   SUM(cache_read_input_tokens) AS cr,
                   MIN(started_at) AS s, MAX(completed_at) AS e
            FROM model_usage
            WHERE status = 'completed'
            GROUP BY session_id""").fetchall()
        # 每会话用量最大的模型
        model_rows = conn.execute("""
            SELECT session_id, model_id, SUM(input_tokens) AS tok
            FROM model_usage
            GROUP BY session_id, model_id
            ORDER BY tok DESC""").fetchall()
        conn.close()
        model_of = {}
        for r in model_rows:
            model_of.setdefault(r["session_id"], r["model_id"])
        for r in usage_rows:
            sid = r["session_id"]
            s = sess.get(sid, {})
            recs.append(_record(
                "zcode", id=sid,
                title=s.get("title") or "(无标题)",
                model=model_of.get(sid, ""),
                cwd=s.get("cwd", ""), source="zcode",
                started_at=_ms_to_s(r["s"]),
                ended_at=_ms_to_s(r["e"] or s.get("updated")),
                input=r["inp"] or 0, output=r["out"] or 0,
                cache_read=r["cr"] or 0, cache_write=r["cw"] or 0,
                reasoning=r["rsn"] or 0,
                api_calls=r["calls"] or 0, message_count=0,
            ))
    except sqlite3.Error:
        pass
    return recs

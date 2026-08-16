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


def _candidate_homes(env_names, default_sub):
    """产出候选 home 路径（环境变量优先，其次 ~/默认子目录）；去重、过滤不存在的目录"""
    seen = set()
    for env in env_names:
        v = os.environ.get(env)
        if v:
            v = os.path.normpath(os.path.expanduser(v))
            if v and v not in seen:
                seen.add(v)
                if os.path.isdir(v):
                    yield v
    d = os.path.normpath(os.path.expanduser(default_sub))
    if d and d not in seen and os.path.isdir(d):
        seen.add(d)
        yield d


def discover():
    """自动发现已安装工具的数据源（启动时扫描，无需手动配置）"""
    out = []
    seen_paths = set()

    def add(type_, name, path):
        if not path:
            return
        path = os.path.normpath(path)
        if path in seen_paths:
            return
        seen_paths.add(path)
        out.append({"type": type_, "name": name, "path": path})

    # --- Hermes: state.db（主实例 + profiles）---
    home = _hermes_home()
    main_db = os.path.join(home, "state.db")
    if os.path.isfile(main_db):
        add("hermes", "default", main_db)
    prof_dir = os.path.join(home, "profiles")
    if os.path.isdir(prof_dir):
        for name in sorted(os.listdir(prof_dir)):
            db = os.path.join(prof_dir, name, "state.db")
            if os.path.isfile(db):
                add("hermes", name, db)

    # --- Codex: state_*.sqlite（CODEX_HOME 或 ~/.codex）---
    for h in _candidate_homes(["CODEX_HOME"], "~/.codex"):
        db = _latest_state_db(h)
        if db:
            add("codex", "codex", db)

    # --- Claude Code: projects 目录（CLAUDE_CONFIG_DIR 或 ~/.claude）---
    for h in _candidate_homes(["CLAUDE_CONFIG_DIR"], "~/.claude"):
        proj = os.path.join(h, "projects")
        if os.path.isdir(proj):
            add("claude", "claude", proj)

    # --- zcode: cli/db/db.sqlite（ZCODE_HOME 或 ~/.zcode）---
    for h in _candidate_homes(["ZCODE_HOME"], "~/.zcode"):
        db = os.path.join(h, "cli", "db", "db.sqlite")
        if os.path.isfile(db):
            add("zcode", "zcode", db)

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
    if not db_path or not os.path.isfile(db_path):
        return recs
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
    """从 ~/.codex/config.toml 读取 model 作为默认模型。

    仅缓存成功读取到的非空值；读取失败返回空串但不缓存，下次调用重试，
    避免启动时 config.toml 被 Codex 写占用导致永久缓存空值（模型显示"未知"）。
    """
    global _codex_model
    if _codex_model:
        return _codex_model
    try:
        cfg = open(os.path.expanduser("~/.codex/config.toml"), encoding="utf-8").read()
        m = re.search(r'^\s*model\s*=\s*"([^"]+)"', cfg, re.MULTILINE)
        if m:
            _codex_model = m.group(1)
            return _codex_model
    except OSError:
        pass
    return ""


def _codex_rollout_start(path):
    """从 rollout 文件名解析会话开始时间（rollout-YYYY-MM-DDTHH-MM-SS-…，本地时区）"""
    p = _clean_win_path(path) or ""
    m = re.search(r"rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})", p)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        int(m.group(4)), int(m.group(5)), int(m.group(6))).timestamp()
    except ValueError:
        return None


def _codex_rollout_hourly(path, fallback_ts=None):
    """读 codex rollout，按 token_count 事件把请求用量归到本地小时桶。

    返回 (buckets, originator, model)：
      buckets: {hour_ts: {input/output/cache_read/cache_write/reasoning/calls}}
    事件无时间戳时用 fallback_ts（线程级兜底时间）归桶；
    无任何用量时 buckets 为空（调用方按 0 消耗会话处理）；文件不可读返回 None。
    """
    p = _clean_win_path(path)
    buckets = {}
    originator = None
    model = None
    prev_total = None
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if d.get("type") == "session_meta":
                    originator = d.get("payload", {}).get("originator")
                elif d.get("type") == "world_state":
                    st = d.get("payload", {}).get("state", {})
                    if isinstance(st, dict) and st.get("model"):
                        model = st["model"]
                elif d.get("type") == "event_msg":
                    pl = d.get("payload", {})
                    if pl.get("type") != "token_count":
                        continue
                    info = pl.get("info") or {}
                    ts = _iso_to_ts(d.get("timestamp")) or fallback_ts
                    if not ts:
                        continue
                    u = info.get("last_token_usage")
                    if u:
                        inp = u.get("input_tokens") or 0
                        out = u.get("output_tokens") or 0
                        cr = u.get("cached_input_tokens") or 0
                        cw = u.get("cache_write_input_tokens") or 0
                        rsn = u.get("reasoning_output_tokens") or 0
                    else:
                        # 只有累计值：用与上一次的差值近似本次请求
                        tot = info.get("total_token_usage") or {}
                        if prev_total is None:
                            inp = tot.get("input_tokens") or 0
                            out = tot.get("output_tokens") or 0
                            cr = tot.get("cached_input_tokens") or 0
                            cw = tot.get("cache_write_input_tokens") or 0
                            rsn = tot.get("reasoning_output_tokens") or 0
                        else:
                            inp = max(0, (tot.get("input_tokens") or 0) - (prev_total.get("input_tokens") or 0))
                            out = max(0, (tot.get("output_tokens") or 0) - (prev_total.get("output_tokens") or 0))
                            cr = max(0, (tot.get("cached_input_tokens") or 0) - (prev_total.get("cached_input_tokens") or 0))
                            cw = max(0, (tot.get("cache_write_input_tokens") or 0) - (prev_total.get("cache_write_input_tokens") or 0))
                            rsn = max(0, (tot.get("reasoning_output_tokens") or 0) - (prev_total.get("reasoning_output_tokens") or 0))
                        prev_total = tot
                    if inp == 0 and out == 0 and cr == 0:
                        continue
                    hour = datetime.fromtimestamp(ts).replace(minute=0, second=0, microsecond=0)
                    key = int(hour.timestamp())
                    b = buckets.get(key)
                    if b is None:
                        b = {"input": 0, "output": 0, "cache_read": 0,
                             "cache_write": 0, "reasoning": 0, "calls": 0}
                        buckets[key] = b
                    b["input"] += inp
                    b["output"] += out
                    b["cache_read"] += cr
                    b["cache_write"] += cw
                    b["reasoning"] += rsn
                    b["calls"] += 1
    except OSError:
        return None
    return buckets, originator, model


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

    rollout 的 token_count 事件带真实时间戳，会话内请求按「本地小时桶」拆分
    （与 zcode 一致，避免长会话或 threads.created_at 异常导致时间归因错误）；
    0 消耗或无 rollout 的会话保留会话级记录。

    default_model: 模型名兜底；不传则读 ~/.codex/config.toml（测试/离线环境可显式传入）"""
    recs = []
    if default_model is None:
        default_model = _codex_default_model()
    if not state_db or not os.path.isfile(state_db):
        return recs
    try:
        conn = sqlite3.connect(state_db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT id, rollout_path, created_at, updated_at, source,
                   model_provider, cwd, title, tokens_used
            FROM threads""").fetchall()
        conn.close()
    except sqlite3.Error:
        return recs
    for r in rows:
        rid = r["id"]
        # threads.created_at 在某些版本不可靠（如 1970），优先 rollout 文件名时间
        fallback_ts = _codex_rollout_start(r["rollout_path"]) or _ms_to_s(r["created_at"])
        detail = _codex_rollout_hourly(r["rollout_path"], fallback_ts)
        if detail is not None:
            buckets, originator, model = detail
            src = _codex_source_from_originator(originator) or _codex_source(r["source"])
            model = model or default_model
            if buckets:
                for hour_ts, b in sorted(buckets.items()):
                    recs.append(_record(
                        "codex", id=f"{rid}@{hour_ts}", _sid=rid,
                        title=r["title"] or "(无标题)",
                        model=model, cwd=_clean_win_path(r["cwd"] or ""),
                        source=src,
                        started_at=hour_ts, ended_at=hour_ts + 3600,
                        input=b["input"], output=b["output"],
                        cache_read=b["cache_read"], cache_write=b["cache_write"],
                        reasoning=b["reasoning"],
                        api_calls=b["calls"], message_count=0,
                    ))
            else:
                # 0 消耗会话：保留会话级记录与真实来源
                recs.append(_record(
                    "codex", id=rid, title=r["title"] or "(无标题)",
                    model=model, cwd=_clean_win_path(r["cwd"] or ""),
                    source=src,
                    started_at=fallback_ts, ended_at=_ms_to_s(r["updated_at"]),
                    input=0, output=0, cache_read=0, cache_write=0,
                    reasoning=0, api_calls=0, message_count=0,
                ))
            continue
        # 降级：rollout 不可读 → 只有总量，按输入计入
        recs.append(_record(
            "codex", id=rid, title=r["title"] or "(无标题)",
            model=default_model, cwd=_clean_win_path(r["cwd"] or ""),
            source=_codex_source(r["source"]),
            started_at=fallback_ts, ended_at=_ms_to_s(r["updated_at"]),
            input=r["tokens_used"] or 0, output=0, cache_read=0, cache_write=0,
            reasoning=0, api_calls=1, message_count=0,
        ))
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
    """Claude Code：projects jsonl 会话按「本地小时桶」拆分。

    Claude Code 的 Task/子代理会写在 <proj>/<session_id>/subagents/*.jsonl 里，
    这些子代理会话的用量一并归入父会话统计（真实会话 id 为 _sid）；
    所有 assistant 消息的用量按消息时间戳归到对应小时桶，避免长会话
    用量堆在会话开始时刻（与 zcode/codex 一致）。

    返回记录 id = f"{session_id}@{hour_ts}"，真实会话 id 存在 _sid 字段；
    0 消耗会话保留会话级记录。"""
    recs = []
    if not projects_dir or not os.path.isdir(projects_dir):
        return recs
    for proj_name in sorted(os.listdir(projects_dir)):
        proj_path = os.path.join(projects_dir, proj_name)
        if not os.path.isdir(proj_path):
            continue
        cwd = _decode_claude_project(proj_name)
        # 收集 (sid -> [jsonl 路径])：主会话文件 + 会话目录下的 subagents/*.jsonl
        files = {}
        for fname in sorted(os.listdir(proj_path)):
            if fname.endswith(".jsonl"):
                files.setdefault(fname[:-6], []).append(os.path.join(proj_path, fname))
        for dname in sorted(os.listdir(proj_path)):
            sub = os.path.join(proj_path, dname, "subagents")
            if not os.path.isdir(sub):
                continue
            for fname in sorted(os.listdir(sub)):
                if fname.endswith(".jsonl"):
                    files.setdefault(dname, []).append(os.path.join(sub, fname))
        for sid, flist in files.items():
            buckets = {}   # hour_ts -> {input, output, cache_read, cache_write, calls, msgs, models}
            hour_msgs = {}   # hour_ts -> 该小时内的全部消息数
            title = ""
            model = ""
            started = ended = None
            last_ts = None
            msgs_total = 0
            for fpath in flist:
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            try:
                                d = json.loads(line)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                continue
                            t = d.get("type")
                            ts = _iso_to_ts(d.get("timestamp")) or last_ts
                            if started is None and ts:
                                started = ts
                            if ts:
                                ended = ts
                                last_ts = ts
                                hkey = int(datetime.fromtimestamp(ts).replace(
                                    minute=0, second=0, microsecond=0).timestamp())
                                hour_msgs[hkey] = hour_msgs.get(hkey, 0) + 1
                            if t == "assistant":
                                msg = d.get("message") or {}
                                u = msg.get("usage") or {}
                                inp = u.get("input_tokens") or 0
                                out = u.get("output_tokens") or 0
                                cr = u.get("cache_read_input_tokens") or 0
                                cw = u.get("cache_creation_input_tokens") or 0
                                if (inp or out or cr or cw) and ts:
                                    key = int(datetime.fromtimestamp(ts).replace(
                                        minute=0, second=0, microsecond=0).timestamp())
                                    b = buckets.get(key)
                                    if b is None:
                                        b = {"input": 0, "output": 0, "cache_read": 0,
                                             "cache_write": 0, "calls": 0, "msgs": 0, "models": {}}
                                        buckets[key] = b
                                    b["input"] += inp
                                    b["output"] += out
                                    b["cache_read"] += cr
                                    b["cache_write"] += cw
                                    b["calls"] += 1
                                    md = msg.get("model") or ""
                                    b["models"][md] = b["models"].get(md, 0) + inp
                                if not model and msg.get("model"):
                                    model = msg["model"]
                            elif t == "user" and not title:
                                txt = _claude_text(d.get("message", {}).get("content"))
                                title = txt[:60] if txt else "(无标题)"
                            msgs_total += 1
                except OSError:
                    continue
            if buckets:
                for hour_ts, b in sorted(buckets.items()):
                    b["msgs"] = hour_msgs.get(hour_ts, 0)
                    bmodel = max(b["models"], key=b["models"].get) if b["models"] else model
                    recs.append(_record(
                        "claude", id=f"{sid}@{hour_ts}", _sid=sid,
                        title=title or "(无标题)", model=bmodel, cwd=cwd, source="claude",
                        started_at=hour_ts, ended_at=hour_ts + 3600,
                        input=b["input"], output=b["output"],
                        cache_read=b["cache_read"], cache_write=b["cache_write"],
                        reasoning=0, api_calls=b["calls"], message_count=b["msgs"],
                    ))
            else:
                # 0 消耗会话：保留会话级记录
                recs.append(_record(
                    "claude", id=sid, title=title or "(无标题)", model=model,
                    cwd=cwd, source="claude",
                    started_at=started, ended_at=ended,
                    input=0, output=0, cache_read=0, cache_write=0,
                    reasoning=0, api_calls=0, message_count=msgs_total,
                ))
    return recs


# ---------------------------------------------------------------------------
# zcode: cli/db/db.sqlite（model_usage 请求级明细）
# ---------------------------------------------------------------------------

def parse_zcode(db_path):
    """zcode 会话按「本地小时桶」拆分。

    长期存活的会话（可能跨数天持续请求）按请求发生时刻归入对应小时桶，
    避免把整个会话的用量记在 MIN(started_at) 上，导致后续日期/时段
    在趋势图与范围汇总中缺失（如"昨天下午没消耗"的假象）。

    返回记录的 id = f"{session_id}@{hour_ts}"，真实会话 id 存在 _sid 字段；
    server 层按 _sid 聚合回会话级视图（会话列表/详情/导出）。
    """
    recs = []
    if not db_path or not os.path.isfile(db_path):
        return recs
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
                "updated": r["time_updated"],
            }
        # 请求级明细：按 (会话, 本地小时) 在 Python 侧聚合
        rows = conn.execute("""
            SELECT session_id, model_id, started_at, completed_at,
                   input_tokens, output_tokens, reasoning_tokens,
                   cache_creation_input_tokens, cache_read_input_tokens
            FROM model_usage WHERE status = 'completed'""").fetchall()
        conn.close()
        buckets = {}   # (session_id, hour_ts) -> 聚合
        for r in rows:
            st = _ms_to_s(r["started_at"])
            if not st:
                continue
            hour = datetime.fromtimestamp(st).replace(minute=0, second=0, microsecond=0)
            key = (r["session_id"], int(hour.timestamp()))
            b = buckets.get(key)
            if b is None:
                b = {
                    "start": int(hour.timestamp()),
                    "end": _ms_to_s(r["completed_at"]) or int(hour.timestamp()) + 3600,
                    "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                    "reasoning": 0, "api_calls": 0, "models": {},
                }
                buckets[key] = b
            b["input"] += r["input_tokens"] or 0
            b["output"] += r["output_tokens"] or 0
            b["reasoning"] += r["reasoning_tokens"] or 0
            b["cache_write"] += r["cache_creation_input_tokens"] or 0
            b["cache_read"] += r["cache_read_input_tokens"] or 0
            b["api_calls"] += 1
            ce = _ms_to_s(r["completed_at"])
            if ce and ce > b["end"]:
                b["end"] = ce
            md = r["model_id"] or ""
            b["models"][md] = b["models"].get(md, 0) + (r["input_tokens"] or 0)
        for (sid, _hour), b in buckets.items():
            s = sess.get(sid, {})
            model = max(b["models"], key=b["models"].get) if b["models"] else ""
            recs.append(_record(
                "zcode", id=f"{sid}@{b['start']}", _sid=sid,
                title=s.get("title") or "(无标题)",
                model=model, cwd=s.get("cwd", ""), source="zcode",
                started_at=b["start"], ended_at=b["end"],
                input=b["input"], output=b["output"],
                cache_read=b["cache_read"], cache_write=b["cache_write"],
                reasoning=b["reasoning"], api_calls=b["api_calls"], message_count=0,
            ))
    except sqlite3.Error:
        pass
    return recs


# ---------------------------------------------------------------------------
# 会话对话消息提取（用于导出 MD）
# ---------------------------------------------------------------------------

def _hermes_messages(db_path, session_id):
    """Hermes: messages 表（role/content/reasoning_content/tool_name），按 timestamp 排序"""
    msgs = []
    if not db_path or not os.path.isfile(db_path):
        return msgs
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT role, content, reasoning_content, tool_name, timestamp
            FROM messages WHERE session_id = ? AND active = 1
            ORDER BY timestamp, id""", (session_id,)).fetchall()
        conn.close()
        for r in rows:
            role = r["role"]
            if role in ("system", "developer"):
                continue
            content = (r["content"] or "").strip()
            reasoning = (r["reasoning_content"] or "").strip()
            if not content and not reasoning:
                continue
            msgs.append({"role": role, "content": content,
                         "reasoning": reasoning, "tool": r["tool_name"] or "",
                         "ts": r["timestamp"]})
    except sqlite3.Error:
        pass
    return msgs


def _codex_content_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                txt = (blk.get("text") or "").strip()
                if txt:
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def _codex_messages(state_db, session_id):
    """Codex: rollout JSONL 的 response_item 事件（message/function_call/output），按行序"""
    msgs = []
    if not state_db or not os.path.isfile(state_db):
        return msgs
    rollout_path = None
    try:
        conn = sqlite3.connect(state_db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute("SELECT rollout_path FROM threads WHERE id = ?", (session_id,)).fetchone()
        conn.close()
        if row:
            rollout_path = row["rollout_path"]
    except sqlite3.Error:
        pass
    if not rollout_path:
        return msgs
    rollout_path = _clean_win_path(rollout_path)
    try:
        with open(rollout_path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                pl = d.get("payload")
                if not isinstance(pl, dict) or d.get("type") != "response_item":
                    continue
                ptype = pl.get("type")
                if ptype == "message":
                    role = pl.get("role") or ""
                    if role in ("developer", "system"):
                        continue
                    text = _codex_content_text(pl.get("content"))
                    if text:
                        msgs.append({"role": role if role in ("user", "assistant") else "assistant",
                                     "content": text, "reasoning": "", "tool": "", "ts": None})
                elif ptype == "function_call":
                    name = pl.get("name") or ""
                    args = pl.get("arguments") or ""
                    msgs.append({"role": "tool", "content": args, "reasoning": "",
                                 "tool": name, "ts": None})
                elif ptype == "function_call_output":
                    out = pl.get("output") or ""
                    msgs.append({"role": "tool", "content": out, "reasoning": "",
                                 "tool": "输出", "ts": None})
    except OSError:
        pass
    return msgs


def _claude_messages(projects_dir, session_id):
    """Claude: projects/<dir>/<session_id>.jsonl，逐行 user/assistant"""
    msgs = []
    if not projects_dir or not os.path.isdir(projects_dir):
        return msgs
    for proj_name in sorted(os.listdir(projects_dir)):
        fpath = os.path.join(projects_dir, proj_name, session_id + ".jsonl")
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    t = d.get("type")
                    if t not in ("user", "assistant"):
                        continue
                    content = _claude_text((d.get("message") or {}).get("content"))
                    if not content:
                        continue
                    msgs.append({"role": t, "content": content, "reasoning": "",
                                 "tool": "", "ts": _iso_to_ts(d.get("timestamp"))})
        except OSError:
            pass
        break
    return msgs


def _zcode_messages(db_path, session_id):
    """zcode: message 表（role+sequence）+ part 表（type=text 正文），按 seq 排序"""
    msgs = []
    if not db_path or not os.path.isfile(db_path):
        return msgs
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        # role 在 message.data 的 JSON 里（message 表无 role 列）
        roles = {}
        for r in conn.execute("SELECT id, data, sequence FROM message WHERE session_id = ?", (session_id,)):
            role = ""
            try:
                d = json.loads(r["data"]) if r["data"] else {}
                if isinstance(d, dict):
                    role = d.get("role") or ""
            except (json.JSONDecodeError, TypeError):
                pass
            roles[r["id"]] = (role, r["sequence"] or 0)
        rows = conn.execute("SELECT message_id, data, sequence FROM part WHERE session_id = ?",
                            (session_id,)).fetchall()
        conn.close()
        for r in rows:
            role, mseq = roles.get(r["message_id"], ("", 0))
            if role not in ("user", "assistant"):
                continue
            try:
                d = json.loads(r["data"]) if r["data"] else {}
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(d, dict) or d.get("type") != "text":
                continue
            text = (d.get("text") or "").strip()
            if not text:
                continue
            msgs.append({"role": role, "content": text, "reasoning": "",
                         "tool": "", "ts": None, "_seq": (mseq, r["sequence"] or 0)})
        msgs.sort(key=lambda m: m.pop("_seq", (0, 0)))
    except sqlite3.Error:
        pass
    return msgs


def extract_session_messages(src, session_id):
    """按数据源类型提取单个会话的对话消息（统一 [{role, content, reasoning, tool, ts}]）"""
    t = src.get("type", "hermes")
    p = src.get("path", "")
    if t == "hermes":
        return _hermes_messages(p, session_id)
    if t == "codex":
        return _codex_messages(p, session_id)
    if t == "claude":
        return _claude_messages(p, session_id)
    if t == "zcode":
        return _zcode_messages(p, session_id)
    return []
# ---------------------------------------------------------------------------
# 请求级数据提取（请求明细页）
# ---------------------------------------------------------------------------


def _req_status(status, error):
    """归一化请求状态：success / error / cancelled"""
    if error:
        return "error"
    s = (status or "").lower()
    if s in ("cancelled", "canceled", "cancelled_by_user"):
        return "cancelled"
    if s in ("error", "failed"):
        return "error"
    return "success"


def _zcode_requests(db_path):
    """zcode：model_usage 表，每行一次 LLM 请求（最完整）"""
    reqs = []
    if not db_path or not os.path.isfile(db_path):
        return reqs
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT session_id, model_id, task_type, status,
                   started_at, duration_ms, time_to_first_token_ms, finish_reason,
                   input_tokens, output_tokens, reasoning_tokens,
                   cache_read_input_tokens, cache_creation_input_tokens,
                   error_type, logical_request_id, attempt_index
            FROM model_usage ORDER BY started_at DESC""").fetchall()
        conn.close()
        for r in rows:
            reqs.append({
                "id": f"zc-{r['logical_request_id'] or r['session_id']}-{r['attempt_index'] or 0}",
                "tool": "zcode",
                "session_id": r["session_id"] or "",
                "model": r["model_id"] or "",
                "task": r["task_type"] or "",
                "input": r["input_tokens"] or 0,
                "output": r["output_tokens"] or 0,
                "reasoning": r["reasoning_tokens"] or 0,
                "cache_read": r["cache_read_input_tokens"] or 0,
                "cache_write": r["cache_creation_input_tokens"] or 0,
                "duration_ms": r["duration_ms"] or 0,
                "ttft_ms": r["time_to_first_token_ms"] or 0,
                "status": _req_status(r["status"], r["error_type"]),
                "finish_reason": r["finish_reason"] or "",
                "error": r["error_type"] or "",
                "started_at": _ms_to_s(r["started_at"]),
            })
    except sqlite3.Error:
        pass
    return reqs


def _claude_requests(projects_dir):
    """Claude Code：projects jsonl 每个 assistant 消息 = 一次请求（含 subagents 子代理，归入父会话）"""
    reqs = []
    if not projects_dir or not os.path.isdir(projects_dir):
        return reqs
    for proj in sorted(os.listdir(projects_dir)):
        d = os.path.join(projects_dir, proj)
        if not os.path.isdir(d):
            continue
        files = []
        for fname in sorted(os.listdir(d)):
            if fname.endswith(".jsonl"):
                files.append((fname[:-6], fname, os.path.join(d, fname)))
        for dname in sorted(os.listdir(d)):
            sub = os.path.join(d, dname, "subagents")
            if os.path.isdir(sub):
                for fname in sorted(os.listdir(sub)):
                    if fname.endswith(".jsonl"):
                        files.append((dname, fname, os.path.join(sub, fname)))
        for sid, fname, fpath in files:
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        try:
                            m = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if m.get("type") != "assistant":
                            continue
                        msg = m.get("message") or {}
                        u = msg.get("usage") or {}
                        reqs.append({
                            "id": m.get("uuid") or f"cl-{fname}-{len(reqs)}",
                            "tool": "claude",
                            "session_id": sid,
                            "model": msg.get("model") or "",
                            "task": "",
                            "input": u.get("input_tokens") or 0,
                            "output": u.get("output_tokens") or 0,
                            "reasoning": 0,
                            "cache_read": u.get("cache_read_input_tokens") or 0,
                            "cache_write": u.get("cache_creation_input_tokens") or 0,
                            "duration_ms": 0,
                            "ttft_ms": 0,
                            "status": "success",
                            "finish_reason": msg.get("stop_reason") or "",
                            "error": "",
                            "started_at": _iso_to_ts(m.get("timestamp")),
                        })
            except OSError:
                pass
    return reqs


def _codex_requests(state_db):
    """Codex：rollout 的 token_count 事件的 last_token_usage = 每次请求用量（含时间戳/reasoning/cache_write）"""
    reqs = []
    if not state_db or not os.path.isfile(state_db):
        return reqs
    try:
        conn = sqlite3.connect(state_db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("SELECT id, rollout_path FROM threads").fetchall()
        conn.close()
    except sqlite3.Error:
        return reqs
    for row in rows:
        sid = row["id"]
        rp = _clean_win_path(row["rollout_path"])
        if not rp or not os.path.isfile(rp):
            continue
        try:
            with open(rp, encoding="utf-8", errors="ignore") as fh:
                model = None
                idx = 0
                for line in fh:
                    try:
                        d = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if d.get("type") == "world_state":
                        st = d.get("payload", {}).get("state", {})
                        if isinstance(st, dict) and st.get("model"):
                            model = st["model"]
                    elif d.get("type") == "event_msg" and d.get("payload", {}).get("type") == "token_count":
                        lu = (d["payload"]["info"].get("last_token_usage") or {})
                        inp = lu.get("input_tokens") or 0
                        out = lu.get("output_tokens") or 0
                        if inp > 0 or out > 0:
                            reqs.append({
                                "id": f"cx-{sid}-{idx}",
                                "tool": "codex",
                                "session_id": sid,
                                "model": model or "",
                                "task": "",
                                "input": inp,
                                "output": out,
                                "reasoning": lu.get("reasoning_output_tokens") or 0,
                                "cache_read": lu.get("cached_input_tokens") or 0,
                                "cache_write": lu.get("cache_write_input_tokens") or 0,
                                "duration_ms": 0,
                                "ttft_ms": 0,
                                "status": "success",
                                "finish_reason": "",
                                "error": "",
                                "started_at": _iso_to_ts(d.get("timestamp")),
                            })
                            idx += 1
        except OSError:
            pass
    return reqs


def _hermes_requests(db_path):
    """Hermes：无请求级 token，降级为「会话×模型×任务」聚合（一行 = api_call_count 次请求）"""
    reqs = []
    if not db_path or not os.path.isfile(db_path):
        return reqs
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT session_id, model, task, api_call_count,
                   input_tokens, output_tokens, reasoning_tokens,
                   cache_read_tokens, cache_write_tokens, first_seen
            FROM session_model_usage ORDER BY first_seen DESC""").fetchall()
        conn.close()
        for r in rows:
            reqs.append({
                "id": f"hm-{r['session_id']}-{r['model']}-{r['task'] or 'main'}",
                "tool": "hermes",
                "session_id": r["session_id"] or "",
                "model": r["model"] or "",
                "task": r["task"] or "",
                "input": r["input_tokens"] or 0,
                "output": r["output_tokens"] or 0,
                "reasoning": r["reasoning_tokens"] or 0,
                "cache_read": r["cache_read_tokens"] or 0,
                "cache_write": r["cache_write_tokens"] or 0,
                "duration_ms": 0,
                "ttft_ms": 0,
                "status": "success",
                "finish_reason": "",
                "error": "",
                "started_at": r["first_seen"] or None,
                "api_calls": r["api_call_count"] or 0,
            })
    except sqlite3.Error:
        pass
    return reqs


def extract_requests(src):
    """按数据源类型提取请求记录（统一 [{id, tool, session_id, model, ...}]）"""
    t = src.get("type", "hermes")
    p = src.get("path", "")
    if t == "zcode":
        return _zcode_requests(p)
    if t == "claude":
        return _claude_requests(p)
    if t == "codex":
        return _codex_requests(p)
    return _hermes_requests(p)
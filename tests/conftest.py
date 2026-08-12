"""pytest 共享 fixtures：四工具 mock 数据工厂（全部落在 tmp_path，不碰真实环境）"""
import json
import sqlite3

import pytest


def _make_hermes_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT, title TEXT, model TEXT, source TEXT, profile_name TEXT, cwd TEXT,
            message_count INTEGER, tool_call_count INTEGER, api_call_count INTEGER,
            input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
            cache_write_tokens INTEGER, reasoning_tokens INTEGER,
            started_at REAL, ended_at REAL)""")
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s1", "测试会话", "gpt-test", "desktop", "default", "/home/user/proj1",
         10, 5, 3, 1000, 500, 2000, 0, 100, 1700000000.0, 1700000100.0))
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s2", "第二个", "gpt-test", "cli", "default", "/home/user/proj2",
         2, 1, 1, 100, 50, 0, 0, 0, 1700000200.0, None))
    conn.commit()
    conn.close()


def _codex_rollout(originator, source, usage=None):
    """构造一行 rollout JSONL 内容"""
    lines = [
        json.dumps({"type": "session_meta",
                    "payload": {"originator": originator, "source": source}},
                   ensure_ascii=False),
    ]
    if usage:
        lines.append(json.dumps({
            "type": "event_msg",
            "payload": {"type": "token_count",
                        "info": {"total_token_usage": usage}},
        }, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _make_codex_dir(tmp):
    db = tmp / "state_5.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE threads (
            id TEXT, rollout_path TEXT, created_at INTEGER, updated_at INTEGER,
            source TEXT, model_provider TEXT, cwd TEXT, title TEXT, tokens_used INTEGER)""")
    # rollout 1：正常会话（token_count 累计 + Codex Desktop）
    r1 = tmp / "rollout1.jsonl"
    r1.write_text(_codex_rollout("Codex Desktop", "vscode", {
        "input_tokens": 100, "cached_input_tokens": 60,
        "output_tokens": 20, "reasoning_output_tokens": 5}), encoding="utf-8")
    # rollout 2：0 消耗会话（无 token_count，只有 meta）
    r2 = tmp / "rollout2.jsonl"
    r2.write_text(_codex_rollout("Codex Desktop", "vscode"), encoding="utf-8")
    # rollout 3：Codex CLI
    r3 = tmp / "rollout3.jsonl"
    r3.write_text(_codex_rollout("Codex CLI", "cli", {
        "input_tokens": 10, "cached_input_tokens": 0,
        "output_tokens": 5, "reasoning_output_tokens": 0}), encoding="utf-8")
    rows = [
        ("t1", str(r1), 1700000000000, 1700000100000, "vscode", "custom",
         "/home/u/p1", "标题一", 125),
        ("t2", str(r2), 1700000200000, 1700000200000, "vscode", "custom",
         "/home/u/p1", "标题二", 0),
        ("t3", str(r3), 1700000300000, 1700000400000, "cli", "custom",
         "/home/u/p2", "标题三", 15),
    ]
    conn.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def _make_claude_dir(tmp):
    proj = tmp / "projects" / "D--work-AI-Proj"
    proj.mkdir(parents=True)
    f1 = proj / "sess1.jsonl"
    f1.write_text(
        json.dumps({"type": "user", "message": {"content": "你好"},
                    "timestamp": "2026-01-01T00:00:00.000Z"}, ensure_ascii=False) + "\n"
        + json.dumps({"type": "assistant",
                      "message": {"model": "claude-x", "usage": {
                          "input_tokens": 10, "output_tokens": 20,
                          "cache_read_input_tokens": 30,
                          "cache_creation_input_tokens": 40}},
                      "timestamp": "2026-01-01T00:00:01.000Z"}) + "\n"
        + json.dumps({"type": "assistant",
                      "message": {"model": "claude-x", "usage": {
                          "input_tokens": 5, "output_tokens": 2,
                          "cache_read_input_tokens": 1,
                          "cache_creation_input_tokens": 0}},
                      "timestamp": "2026-01-01T00:00:02.000Z"}) + "\n",
        encoding="utf-8")
    f2 = proj / "sess2.jsonl"
    f2.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"},
                    "timestamp": "2026-01-02T00:00:00.000Z"}) + "\n",
        encoding="utf-8")
    return tmp / "projects"


def _make_zcode_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE session (id TEXT, directory TEXT, title TEXT, "
                 "time_created INTEGER, time_updated INTEGER)")
    conn.execute("CREATE TABLE model_usage (session_id TEXT, model_id TEXT, "
                 "input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, "
                 "cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER, "
                 "started_at INTEGER, completed_at INTEGER, status TEXT, tool_call_count INTEGER)")
    conn.execute("INSERT INTO session VALUES (?,?,?,?,?)",
                 ("z1", "/home/u/p3", "zcode 会话", 1700000000000, 1700000100000))
    conn.execute("INSERT INTO session VALUES (?,?,?,?,?)",
                 ("z2", "/home/u/p4", "另一会话", 1700000200000, 1700000200000))
    conn.executemany(
        "INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [("z1", "GLM-X", 100, 10, 5, 50, 20, 1700000000000, 1700000050000, "completed", 2),
         ("z1", "GLM-X", 200, 20, 0, 0, 100, 1700000050000, 1700000090000, "completed", 1),
         ("z1", "GLM-Y", 50, 5, 0, 0, 0, 1700000090000, 1700000100000, "rate_limited", 0),
         ("z2", "GLM-X", 10, 1, 0, 0, 0, 1700000200000, 1700000200000, "completed", 1)])
    conn.commit()
    conn.close()


@pytest.fixture
def hermes_db(tmp_path):
    p = tmp_path / "hermes" / "state.db"
    p.parent.mkdir()
    _make_hermes_db(str(p))
    return p


@pytest.fixture
def codex_db(tmp_path):
    return _make_codex_dir(tmp_path)


@pytest.fixture
def claude_projects(tmp_path):
    return _make_claude_dir(tmp_path)


@pytest.fixture
def zcode_db(tmp_path):
    p = tmp_path / "zcode" / "db.sqlite"
    p.parent.mkdir()
    _make_zcode_db(str(p))
    return p

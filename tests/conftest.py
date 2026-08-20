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
    # messages 表（导出 MD 用）
    conn.execute("""CREATE TABLE messages (
        id TEXT, session_id TEXT, role TEXT, content TEXT, reasoning_content TEXT,
        tool_name TEXT, timestamp REAL, active INTEGER)""")
    conn.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
        [("m1", "s1", "user", "你好", "", None, 1700000000.0, 1),
         ("m2", "s1", "assistant", "你好！", "思考中", None, 1700000001.0, 1),
         ("m3", "s1", "tool", "命令输出", "", "terminal", 1700000002.0, 1),
         ("m4", "s1", "system", "系统提示", "", None, 1700000003.0, 1)])
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
    # rollout 1：正常会话（token_count 累计 + Codex Desktop + 对话消息）
    r1 = tmp / "rollout1.jsonl"
    r1.write_text(
        _codex_rollout("Codex Desktop", "vscode", {
            "input_tokens": 100, "cached_input_tokens": 60,
            "output_tokens": 20, "reasoning_output_tokens": 5})
        + json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "帮我写代码"}]}}) + "\n"
        + json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "好的"}]}}) + "\n"
        + json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "developer",
            "content": [{"type": "input_text", "text": "系统指令"}]}}) + "\n",
        encoding="utf-8")
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
                      "message": {"model": "claude-x", "content": "你好，有什么可以帮你？",
                                  "usage": {
                                      "input_tokens": 10, "output_tokens": 20,
                                      "cache_read_input_tokens": 30,
                                      "cache_creation_input_tokens": 40}},
                      "timestamp": "2026-01-01T00:00:01.000Z"}, ensure_ascii=False) + "\n"
        + json.dumps({"type": "assistant",
                      "message": {"model": "claude-x", "content": "补充说明",
                                  "usage": {
                                      "input_tokens": 5, "output_tokens": 2,
                                      "cache_read_input_tokens": 1,
                                      "cache_creation_input_tokens": 0}},
                      "timestamp": "2026-01-01T00:00:02.000Z"}, ensure_ascii=False) + "\n",
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
                 "computed_total_tokens INTEGER, "
                 "started_at INTEGER, completed_at INTEGER, status TEXT, tool_call_count INTEGER)")
    conn.execute("INSERT INTO session VALUES (?,?,?,?,?)",
                 ("z1", "/home/u/p3", "zcode 会话", 1700000000000, 1700000100000))
    conn.execute("INSERT INTO session VALUES (?,?,?,?,?)",
                 ("z2", "/home/u/p4", "另一会话", 1700000200000, 1700000200000))
    # computed_total_tokens = input + output（zcode 的包含式口径：input 已含缓存）
    conn.executemany(
        "INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [("z1", "GLM-X", 100, 10, 5, 50, 20, 110, 1700000000000, 1700000050000, "completed", 2),
         ("z1", "GLM-X", 200, 20, 0, 0, 100, 220, 1700000050000, 1700000090000, "completed", 1),
         ("z1", "GLM-Y", 50, 5, 0, 0, 0, 55, 1700000090000, 1700000100000, "rate_limited", 0),
         ("z2", "GLM-X", 10, 1, 0, 0, 0, 11, 1700000200000, 1700000200000, "completed", 1)])
    # message + part 表（导出 MD 用；role 在 message.data 的 JSON 里）
    conn.execute("CREATE TABLE message (id TEXT, session_id TEXT, data TEXT, sequence INTEGER)")
    conn.execute("CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, data TEXT, sequence INTEGER)")
    conn.executemany(
        "INSERT INTO message VALUES (?,?,?,?)",
        [("msg1", "z1", json.dumps({"role": "user"}), 0),
         ("msg2", "z1", json.dumps({"role": "assistant"}), 1)])
    conn.executemany(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        [("p1", "msg1", "z1", json.dumps({"type": "text", "text": "你好"}), 0),
         ("p2", "msg2", "z1", json.dumps({"type": "text", "text": "你好！"}), 0),
         ("p3", "msg2", "z1", json.dumps({"type": "step-start"}), 1)])
    conn.commit()
    conn.close()


def _dsh_session_lines(session_id, cwd, created_ms, title=None, msgs=None,
                       header_model=None, seed_length=0):
    """构造 DSH session.jsonl 事件流行列表"""
    lines = [json.dumps({
        "type": "session", "version": 0, "id": session_id,
        "createdAt": created_ms, "cwd": cwd, "delegationDepth": 0,
        **({"seedLength": seed_length} if seed_length else {}),
    }, ensure_ascii=False)]
    if title:
        lines.append(json.dumps({"type": "session/title", "seq": 1,
                                 "data": {"title": title}}, ensure_ascii=False))
    if header_model:
        lines.append(json.dumps({
            "type": "request/header", "seq": 2, "time": created_ms + 1,
            "data": {"header": {"config": {"provider": "test", "model": header_model}}},
        }, ensure_ascii=False))
    for i, m in enumerate(msgs or []):
        role = m.get("role", "assistant")
        if role == "user":
            lines.append(json.dumps({
                "type": "user/message", "seq": 10 + i, "time": m["time"],
                "data": {"content": [{"type": "text", "text": m.get("text", "")}],
                         "source": {"kind": "user"}, "role": "user",
                         "id": m.get("id", f"u-{i}")},
            }, ensure_ascii=False))
        else:
            content = []
            if m.get("reasoning_text"):
                content.append({"type": "reasoning", "text": m["reasoning_text"]})
            if m.get("text"):
                content.append({"type": "text", "text": m["text"]})
            lines.append(json.dumps({
                "type": "assistant/message", "seq": 20 + i, "time": m["time"],
                "data": {"turn": 1, "step": i + 1,
                         "message": {"role": "assistant",
                                     "content": content,
                                     "source": {"provider": "test",
                                                "model": m.get("model", "")},
                                     "id": m.get("id", f"a-{i}")},
                         "usage": m.get("usage", {})},
            }, ensure_ascii=False))
    return lines


def _make_dsh_dir(tmp):
    """构造 DSH sessions 目录：明文 + zstd + 0 消耗 + fork 会话"""
    root = tmp / "dsh" / "sessions"
    # 会话 1：明文 session.jsonl，2 条 assistant（含缓存/推理）
    d1 = root / "--proj-one--" / "session-aaa"
    d1.mkdir(parents=True)
    (d1 / "session.jsonl").write_text("\n".join(_dsh_session_lines(
        "session-aaa", "/home/u/proj1", 1700000000000, title="DSH 会话一",
        header_model="deepseek-v4-pro",
        msgs=[
            {"role": "user", "time": 1700000001000, "text": "帮我写代码"},
            {"role": "assistant", "time": 1700000010000, "model": "deepseek-v4-pro",
             "text": "好的，这是代码", "reasoning_text": "思考中",
             "usage": {"inputTokens": 100, "outputTokens": 50,
                       "cacheReadTokens": 20, "reasoningTokens": 5}},
            {"role": "assistant", "time": 1700000020000, "model": "deepseek-v4-pro",
             "text": "完成了",
             "usage": {"inputTokens": 300, "outputTokens": 30,
                       "cacheReadTokens": 100}},
        ])) + "\n", encoding="utf-8")
    # 会话 2：zstd 压缩 session.jsonl.zstd
    d2 = root / "--proj-two--" / "session-bbb"
    d2.mkdir(parents=True)
    content = "\n".join(_dsh_session_lines(
        "session-bbb", "/home/u/proj2", 1700000100000, title="DSH 会话二",
        header_model="deepseek-v4-flash",
        msgs=[
            {"role": "user", "time": 1700000101000, "text": "zstd 会话"},
            {"role": "assistant", "time": 1700000110000, "model": "deepseek-v4-flash",
             "usage": {"inputTokens": 50, "outputTokens": 10}},
        ])) + "\n"
    try:
        import zstandard
        (d2 / "session.jsonl.zstd").write_bytes(
            zstandard.ZstdCompressor().compress(content.encode("utf-8")))
    except ImportError:  # pragma: no cover - 无 zstandard 时跳过 zstd 会话
        (d2 / "session.jsonl").write_text(content, encoding="utf-8")
    # 会话 3：0 消耗（只有 session 事件）
    d3 = root / "--proj-three--" / "session-ccc"
    d3.mkdir(parents=True)
    (d3 / "session.jsonl").write_text(
        json.dumps({"type": "session", "id": "session-ccc",
                    "createdAt": 1700000200000, "cwd": "/home/u/proj3"}) + "\n",
        encoding="utf-8")
    return root


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


@pytest.fixture
def dsh_sessions(tmp_path):
    return _make_dsh_dir(tmp_path)

"""请求级数据提取（请求明细页）单元测试"""
import json
import sqlite3

import parsers


def _src(type_, path):
    return {"type": type_, "name": type_, "path": str(path), "auto": False}


def test_claude_requests(claude_projects):
    reqs = parsers.extract_requests(_src("claude", claude_projects))
    assert len(reqs) >= 2
    r = reqs[0]
    assert r["tool"] == "claude"
    assert r["model"] == "claude-x"
    assert r["input"] == 10
    assert r["output"] == 20
    assert r["cache_read"] == 30
    assert r["cache_write"] == 40


def test_codex_requests(tmp_path):
    r = tmp_path / "rollout.jsonl"
    r.write_text("\n".join([
        json.dumps({"timestamp": "2026-01-01T10:00:00.000Z", "type": "world_state",
                    "payload": {"state": {"model": "gpt-5.6-sol"}}}),
        json.dumps({"timestamp": "2026-01-01T10:00:01.000Z", "type": "event_msg",
                    "payload": {"type": "token_count", "info": {"last_token_usage": {
                        "input_tokens": 100, "output_tokens": 50,
                        "cached_input_tokens": 30, "cache_write_input_tokens": 10,
                        "reasoning_output_tokens": 5}}}}),
        json.dumps({"timestamp": "2026-01-01T10:00:02.000Z", "type": "event_msg",
                    "payload": {"type": "token_count", "info": {"last_token_usage": {
                        "input_tokens": 0, "output_tokens": 0}}}}),
    ]), encoding="utf-8")
    db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
    conn.execute("INSERT INTO threads VALUES (?, ?)", ("t1", str(r)))
    conn.commit()
    conn.close()

    reqs = parsers.extract_requests(_src("codex", db))
    assert len(reqs) == 1  # 第二条 token_count 无消耗被过滤
    x = reqs[0]
    assert x["model"] == "gpt-5.6-sol"
    assert x["input"] == 100
    assert x["output"] == 50
    assert x["cache_read"] == 30
    assert x["cache_write"] == 10
    assert x["reasoning"] == 5
    assert x["started_at"] is not None


def test_codex_requests_null_payload_info(tmp_path):
    """回归：payload 为 None / info 为 None / info 缺失时不应崩溃（曾致请求明细页 500）"""
    r = tmp_path / "rollout.jsonl"
    r.write_text("\n".join([
        # world_state 且 payload=None
        json.dumps({"timestamp": "2026-01-01T10:00:00.000Z", "type": "world_state", "payload": None}),
        # token_count 且 info=None
        json.dumps({"timestamp": "2026-01-01T10:00:01.000Z", "type": "event_msg",
                    "payload": {"type": "token_count", "info": None}}),
        # token_count 且 info 缺失
        json.dumps({"timestamp": "2026-01-01T10:00:02.000Z", "type": "event_msg",
                    "payload": {"type": "token_count"}}),
        # 正常 token_count（应被计入）
        json.dumps({"timestamp": "2026-01-01T10:00:03.000Z", "type": "event_msg",
                    "payload": {"type": "token_count", "info": {"last_token_usage": {
                        "input_tokens": 100, "output_tokens": 50}}}}),
    ]), encoding="utf-8")
    db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
    conn.execute("INSERT INTO threads VALUES (?, ?)", ("t1", str(r)))
    conn.commit()
    conn.close()

    reqs = parsers.extract_requests(_src("codex", db))
    assert len(reqs) == 1  # 仅正常那条被计入，其余空值被安全跳过
    assert reqs[0]["input"] == 100
    assert reqs[0]["output"] == 50


def test_zcode_requests(tmp_path):
    db = tmp_path / "zcode.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE model_usage (
        session_id TEXT, model_id TEXT, task_type TEXT, status TEXT,
        started_at INTEGER, duration_ms INTEGER, time_to_first_token_ms INTEGER,
        finish_reason TEXT, input_tokens INTEGER, output_tokens INTEGER,
        reasoning_tokens INTEGER, cache_read_input_tokens INTEGER,
        cache_creation_input_tokens INTEGER, error_type TEXT,
        logical_request_id TEXT, attempt_index INTEGER)""")
    conn.execute("INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("z1", "GLM-X", "chat", "completed", 1700000000000, 1500, 300,
                  "stop", 100, 50, 20, 30, 10, None, "req1", 0))
    conn.commit()
    conn.close()

    reqs = parsers.extract_requests(_src("zcode", db))
    assert len(reqs) == 1
    r = reqs[0]
    assert r["model"] == "GLM-X"
    assert r["task"] == "chat"
    assert r["input"] == 100
    assert r["duration_ms"] == 1500
    assert r["ttft_ms"] == 300
    assert r["status"] == "success"
    assert r["started_at"] == 1700000000.0


def test_hermes_requests(tmp_path):
    db = tmp_path / "hermes.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE session_model_usage (
        session_id TEXT, model TEXT, task TEXT, api_call_count INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
        cache_read_tokens INTEGER, cache_write_tokens INTEGER, first_seen REAL)""")
    conn.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("s1", "gpt-test", "main", 3, 100, 50, 20, 30, 10, 1700000000.0))
    conn.commit()
    conn.close()

    reqs = parsers.extract_requests(_src("hermes", db))
    assert len(reqs) == 1
    r = reqs[0]
    assert r["model"] == "gpt-test"
    assert r["task"] == "main"
    assert r["api_calls"] == 3
    assert r["input"] == 100

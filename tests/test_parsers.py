"""四工具解析器单元测试（mock 数据）"""
import json

import parsers


def _src(type_, path):
    return {"type": type_, "name": type_, "path": str(path), "auto": False}


class TestHermes:
    def test_parse_fields(self, hermes_db):
        recs = parsers.parse_hermes(str(hermes_db))
        assert len(recs) == 2
        r = recs[0]
        assert r["tool"] == "hermes"
        assert r["id"] == "s1"
        assert r["title"] == "测试会话"
        assert r["model"] == "gpt-test"
        assert r["source"] == "desktop"
        assert r["cwd"] == "/home/user/proj1"
        assert r["input"] == 1000
        assert r["output"] == 500
        assert r["cache_read"] == 2000
        assert r["cache_write"] == 0
        assert r["reasoning"] == 100
        assert r["api_calls"] == 3
        assert r["message_count"] == 10
        assert r["started_at"] == 1700000000.0
        assert r["ended_at"] == 1700000100.0

    def test_none_end(self, hermes_db):
        recs = parsers.parse_hermes(str(hermes_db))
        assert recs[1]["ended_at"] is None

    def test_missing_file(self, tmp_path):
        assert parsers.parse_hermes(str(tmp_path / "nope.db")) == []


class TestCodex:
    def test_full_parse(self, codex_db):
        recs = parsers.parse_codex(str(codex_db), default_model="gpt-test")
        assert len(recs) == 3
        by_id = {r.get("_sid") or r["id"]: r for r in recs}

        r1 = by_id["t1"]
        assert r1["model"] == "gpt-test"    # 显式传入，不依赖本机 config.toml
        assert r1["source"] == "desktop"    # Codex Desktop -> desktop
        assert r1["input"] == 100
        assert r1["output"] == 20
        assert r1["cache_read"] == 60
        assert r1["reasoning"] == 5
        assert r1["api_calls"] == 1
        assert r1["cwd"] == "/home/u/p1"
        assert r1["id"].startswith("t1@")   # 按小时桶拆分，id 带小时后缀
        # 事件无时间戳 → created_at(1700000000) 兜底归小时桶，started_at=小时起点
        assert r1["started_at"] == 1700000000 - (1700000000 % 3600)

    def test_zero_usage_keeps_source(self, codex_db):
        recs = {r.get("_sid") or r["id"]: r for r in parsers.parse_codex(str(codex_db), default_model="gpt-test")}
        r2 = recs["t2"]
        assert r2["input"] == 0
        assert r2["output"] == 0
        assert r2["source"] == "desktop"    # 0 消耗会话仍按 originator 归源

    def test_cli_originator(self, codex_db):
        recs = {r.get("_sid") or r["id"]: r for r in parsers.parse_codex(str(codex_db), default_model="gpt-test")}
        assert recs["t3"]["source"] == "cli"    # Codex CLI -> cli

    def test_hour_split(self, tmp_path):
        """带时间戳的 token_count 事件按请求时刻归到不同小时桶"""
        import sqlite3
        db = tmp_path / "state.sqlite"
        r = tmp_path / "r.jsonl"
        r.write_text("\n".join([
            json.dumps({"type": "session_meta", "payload": {"originator": "Codex CLI", "source": "cli"}}),
            json.dumps({"timestamp": "2026-01-01T10:05:00.000Z", "type": "event_msg",
                        "payload": {"type": "token_count", "info": {"last_token_usage": {
                            "input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 50,
                            "reasoning_output_tokens": 5}}}}),
            json.dumps({"timestamp": "2026-01-01T11:15:00.000Z", "type": "event_msg",
                        "payload": {"type": "token_count", "info": {"last_token_usage": {
                            "input_tokens": 200, "output_tokens": 20}}}}),
        ]), encoding="utf-8")
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, created_at INTEGER, updated_at INTEGER, source TEXT, model_provider TEXT, cwd TEXT, title TEXT, tokens_used INTEGER)")
        conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)",
                     ("t1", str(r), 1700000000000, 1700000100000, "cli", "custom", "/p", "T", 300))
        conn.commit()
        conn.close()
        recs = parsers.parse_codex(str(db), default_model="gpt-test")
        assert len(recs) == 2                    # 两个小时桶
        assert len({x["started_at"] for x in recs}) == 2
        assert sum(x["input"] for x in recs) == 300
        assert all(x["_sid"] == "t1" for x in recs)

    def test_orphan_archived_rollout(self, tmp_path):
        """archived_sessions 下未被 state DB 引用的孤儿 rollout 也应被统计"""
        import sqlite3
        home = tmp_path / "codex"
        (home / "archived_sessions").mkdir(parents=True)
        (home / "sessions").mkdir(parents=True)
        db = home / "state_5.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, created_at INTEGER, updated_at INTEGER, source TEXT, model_provider TEXT, cwd TEXT, title TEXT, tokens_used INTEGER)")
        # state DB 只引用 sessions/ 下的一个 rollout
        ref = home / "sessions" / "rollout-2026-01-01T09-00-00-ref.jsonl"
        ref.write_text(json.dumps({"type": "session_meta",
                                   "payload": {"originator": "Codex CLI", "source": "cli",
                                               "id": "t1", "cwd": "/p"}}) + "\n",
                       encoding="utf-8")
        conn.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)",
                     ("t1", str(ref), 1700000000000, 1700000100000, "cli", "openai", "/p", "ref", 0))
        conn.commit()
        conn.close()
        # 孤儿 rollout（archived_sessions，不在 state DB）
        orphan = home / "archived_sessions" / "rollout-2026-01-01T10-00-00-abc.jsonl"
        orphan.write_text(
            json.dumps({"type": "session_meta",
                        "payload": {"originator": "Codex Desktop", "source": "vscode",
                                    "id": "orphan-1", "cwd": "/orphan"}}) + "\n"
            + json.dumps({"timestamp": "2026-01-01T10:00:00.000Z", "type": "event_msg",
                          "payload": {"type": "token_count",
                                      "info": {"last_token_usage": {
                                          "input_tokens": 77, "output_tokens": 7}}}}) + "\n",
            encoding="utf-8")
        recs = parsers.parse_codex(str(db), default_model="gpt-test")
        by_sid = {r.get("_sid") or r["id"]: r for r in recs}
        assert "t1" in by_sid
        assert "orphan-1" in by_sid              # 孤儿会话被纳入
        o = by_sid["orphan-1"]
        assert o["input"] == 77
        assert o["output"] == 7
        assert o["source"] == "desktop"          # originator=Codex Desktop
        assert o["cwd"] == "/orphan"


class TestClaude:
    def test_parse_accumulate(self, claude_projects):
        recs = {r["title"]: r for r in parsers.parse_claude(str(claude_projects))}
        r = recs["你好"]
        assert r["tool"] == "claude"
        assert r["cwd"] == r"D:\work\AI\Proj"  # D--work-AI-Proj 解码
        assert r["input"] == 15        # 10 + 5
        assert r["output"] == 22       # 20 + 2
        assert r["cache_read"] == 31   # 30 + 1
        assert r["cache_write"] == 40  # 40 + 0
        assert r["api_calls"] == 2
        assert r["model"] == "claude-x"
        assert r["message_count"] == 3

    def test_user_only_session_skipped(self, claude_projects):
        """纯 user 会话（无 assistant 消息，如 /usage 本地命令会话）不生成记录"""
        recs = {r["title"]: r for r in parsers.parse_claude(str(claude_projects))}
        assert "hi" not in recs          # sess2 纯 user、无 assistant 消息 → 跳过
        assert "你好" in recs             # sess1 有 assistant 消息 → 保留

    def test_zero_usage_assistant_kept(self, tmp_path):
        """有 assistant 消息但 0 usage（如 <synthetic> 合成消息）仍生成记录"""
        proj = tmp_path / "projects" / "D--work-AI-Proj"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "q"},
                        "timestamp": "2026-01-01T00:00:00.000Z"}) + "\n"
            + json.dumps({"type": "assistant",
                          "message": {"model": "<synthetic>",
                                      "content": "No response requested.",
                                      "usage": {"input_tokens": 0, "output_tokens": 0,
                                                "cache_read_input_tokens": 0,
                                                "cache_creation_input_tokens": 0}},
                          "timestamp": "2026-01-01T00:00:05.000Z"}) + "\n",
            encoding="utf-8")
        recs = parsers.parse_claude(str(tmp_path / "projects"))
        assert len(recs) == 1
        assert recs[0]["model"] == "<synthetic>"
        assert recs[0]["input"] == 0
        assert recs[0]["api_calls"] == 0

    def test_subagents_included(self, tmp_path):
        """subagents/ 目录下的子代理会话用量应归入父会话（_sid 一致）"""
        import datetime as dt
        proj = tmp_path / "projects" / "D--work-AI-Proj"
        sub = proj / "sess1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-abc.jsonl").write_text(
            json.dumps({"type": "assistant",
                        "message": {"model": "claude-x", "content": "x",
                                    "usage": {"input_tokens": 100, "output_tokens": 5,
                                              "cache_read_input_tokens": 90}},
                        "timestamp": "2026-01-01T00:00:05.000Z"}) + "\n",
            encoding="utf-8")
        recs = parsers.parse_claude(str(tmp_path / "projects"))
        sess_recs = [r for r in recs if r.get("_sid") == "sess1"]
        assert len(sess_recs) == 1                    # 同一小时合并为一桶
        assert sess_recs[0]["input"] == 100
        assert sess_recs[0]["output"] == 5
        assert sess_recs[0]["cache_read"] == 90
        assert sess_recs[0]["id"].startswith("sess1@")

    def test_hour_split(self, tmp_path):
        """长会话跨小时：用量按消息时间戳归到不同小时桶"""
        proj = tmp_path / "projects" / "D--work-AI-Proj"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text("\n".join([
            json.dumps({"type": "assistant",
                        "message": {"model": "m", "usage": {"input_tokens": 100}},
                        "timestamp": "2026-01-01T10:00:00.000Z"}),
            json.dumps({"type": "assistant",
                        "message": {"model": "m", "usage": {"input_tokens": 200}},
                        "timestamp": "2026-01-01T11:00:00.000Z"}),
        ]) + "\n", encoding="utf-8")
        recs = parsers.parse_claude(str(tmp_path / "projects"))
        assert len(recs) == 2
        assert len({r["started_at"] for r in recs}) == 2
        assert sum(r["input"] for r in recs) == 300
        assert all(r.get("_sid") == "s1" for r in recs)

    def test_transcripts_dir(self, tmp_path):
        """同级 transcripts/ 目录的裸转录文件（ses_*.jsonl）也应被统计（cwd 未知）"""
        projects = tmp_path / "projects"
        projects.mkdir()
        transcripts = tmp_path / "transcripts"
        transcripts.mkdir()
        (transcripts / "ses_abc.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "转录问题"},
                        "timestamp": "2026-01-01T00:00:00.000Z"}) + "\n"
            + json.dumps({"type": "assistant",
                          "message": {"model": "claude-t", "content": "回答",
                                      "usage": {"input_tokens": 33, "output_tokens": 4,
                                                "cache_read_input_tokens": 2}},
                          "timestamp": "2026-01-01T00:00:05.000Z"}) + "\n",
            encoding="utf-8")
        recs = parsers.parse_claude(str(projects))
        t_recs = [r for r in recs if r.get("_sid") == "ses_abc"]
        assert len(t_recs) == 1
        assert t_recs[0]["cwd"] == ""            # 裸转录无项目目录，cwd 未知
        assert t_recs[0]["input"] == 33
        assert t_recs[0]["output"] == 4
        assert t_recs[0]["cache_read"] == 2
        assert t_recs[0]["model"] == "claude-t"


class TestZcode:
    def test_aggregate(self, zcode_db):
        recs = parsers.parse_zcode(str(zcode_db))
        assert len(recs) == 2
        by_id = {r["_sid"]: r for r in recs}
        z1 = by_id["z1"]
        # 只统计 status=completed：GLM-X 两次 + GLM-Y rate_limited 排除
        # zcode 的 input 为缓存包含式，归一化后：
        #   行1 net_in=100-50-20=30, net_out=10-5=5
        #   行2 net_in=200-100-0=100, net_out=20-0=20
        assert z1["input"] == 130        # 30 + 100
        assert z1["output"] == 25        # 5 + 20
        assert z1["reasoning"] == 5
        assert z1["cache_write"] == 50   # cache_creation
        assert z1["cache_read"] == 120   # 20 + 100
        assert z1["api_calls"] == 2
        assert z1["model"] == "GLM-X"    # 用量最大的模型
        assert z1["title"] == "zcode 会话"
        assert z1["id"].startswith("z1@")     # 按小时桶拆分，id 带小时后缀
        # started_at = 请求所在小时起点（1700000000 落在 1700000000-1700000000%3600 桶）
        assert z1["started_at"] == 1700000000 - (1700000000 % 3600)

    def test_other_session(self, zcode_db):
        recs = {r["_sid"]: r for r in parsers.parse_zcode(str(zcode_db))}
        assert recs["z2"]["input"] == 10
        assert recs["z2"]["api_calls"] == 1

    def test_hour_split(self, tmp_path):
        """长期存活会话跨小时：用量按请求时刻归到不同小时桶，而非全部记在开始时间"""
        import sqlite3
        db = tmp_path / "zc.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE session (id TEXT, directory TEXT, title TEXT, "
                     "time_created INTEGER, time_updated INTEGER)")
        conn.execute("CREATE TABLE model_usage (session_id TEXT, model_id TEXT, "
                     "input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, "
                     "cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER, "
                     "computed_total_tokens INTEGER, "
                     "started_at INTEGER, completed_at INTEGER, status TEXT, tool_call_count INTEGER)")
        conn.execute("INSERT INTO session VALUES (?,?,?,?,?)",
                     ("s1", "/p", "跨小时", 1700000000000, 1700000000000))
        # 同一会话在三个相邻小时各发生一次请求（无缓存，computed_total=in+out）
        h0, h1, h2 = 1700000000, 1700003600, 1700007200
        conn.executemany(
            "INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [("s1", "M", 100, 1, 0, 0, 0, 101, h0 * 1000, h0 * 1000, "completed", 0),
             ("s1", "M", 200, 2, 0, 0, 0, 202, h1 * 1000, h1 * 1000, "completed", 0),
             ("s1", "M", 300, 3, 0, 0, 0, 303, h2 * 1000, h2 * 1000, "completed", 0)])
        conn.commit()
        conn.close()
        recs = parsers.parse_zcode(str(db))
        assert len(recs) == 3                     # 三个小时桶，而非 1 个会话记录
        starts = sorted(r["started_at"] for r in recs)
        assert starts == [h - (h % 3600) for h in (h0, h1, h2)]   # 各自小时起点
        assert {r["_sid"] for r in recs} == {"s1"}
        assert sum(r["input"] for r in recs) == 600


class TestDsh:
    def test_parse_all(self, dsh_sessions):
        recs = parsers.parse_dsh(str(dsh_sessions))
        assert len(recs) == 3
        by_sid = {r["_sid"]: r for r in recs}
        # 会话 1（明文）：2 条 assistant
        a = by_sid["session-aaa"]
        assert a["tool"] == "dsh"
        assert a["title"] == "DSH 会话一"
        assert a["model"] == "deepseek-v4-pro"
        assert a["cwd"] == "/home/u/proj1"
        assert a["input"] == 400          # 100 + 300
        assert a["output"] == 75          # (50-5) + 30（reasoning 是 output 子集）
        assert a["cache_read"] == 120     # 20 + 100
        assert a["reasoning"] == 5
        assert a["api_calls"] == 2
        assert a["id"].startswith("session-aaa@")
        # 会话 2（zstd）：1 条 assistant
        b = by_sid["session-bbb"]
        assert b["model"] == "deepseek-v4-flash"
        assert b["cwd"] == "/home/u/proj2"
        assert b["input"] == 50
        assert b["output"] == 10
        assert b["api_calls"] == 1
        # 会话 3（0 消耗）
        c = by_sid["session-ccc"]
        assert c["input"] == 0
        assert c["output"] == 0
        assert c["api_calls"] == 0
        assert c["title"] == "(无标题)"

    def test_messages(self, dsh_sessions):
        src = {"type": "dsh", "path": str(dsh_sessions)}
        msgs = parsers.extract_session_messages(src, "session-aaa")
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles
        user = [m for m in msgs if m["role"] == "user"][0]
        assert user["content"] == "帮我写代码"

    def test_requests(self, dsh_sessions):
        src = {"type": "dsh", "path": str(dsh_sessions)}
        reqs = parsers.extract_requests(src)
        # session-aaa 2 条 + session-bbb 1 条 = 3 条
        assert len(reqs) == 3
        a_reqs = [q for q in reqs if q["session_id"] == "session-aaa"]
        assert len(a_reqs) == 2
        assert sum(q["input"] for q in a_reqs) == 400
        # output 为净值（已减 reasoning）
        assert a_reqs[0]["output"] == 45   # 50 - 5
        assert a_reqs[0]["reasoning"] == 5
        assert a_reqs[0]["model"] == "deepseek-v4-pro"

    def test_missing_dir(self, tmp_path):
        assert parsers.parse_dsh(str(tmp_path / "nope")) == []


class TestHelpers:
    def test_decode_claude_project_windows(self):
        assert parsers._decode_claude_project("D--work-AI-AiAgent-agent") \
            == r"D:\work\AI\AiAgent\agent"

    def test_decode_claude_project_posix(self):
        assert parsers._decode_claude_project("-home-user-proj") == "/home/user/proj"

    def test_ms_to_s(self):
        assert parsers._ms_to_s(1700000000000) == 1700000000.0   # 毫秒
        assert parsers._ms_to_s(1700000000) == 1700000000.0      # 秒
        assert parsers._ms_to_s(None) is None

    def test_iso_to_ts(self):
        assert abs(parsers._iso_to_ts("2026-01-01T00:00:00.000Z") - 1767225600.0) < 2
        assert parsers._iso_to_ts("bad") is None

    def test_codex_source_json_subagent(self):
        assert parsers._codex_source('{"subagent":{"thread_spawn":{}}}') == "subagent"
        assert parsers._codex_source("vscode") == "vscode"

    def test_candidate_homes_env_priority(self, monkeypatch, tmp_path):
        custom = tmp_path / 'custom_codex'
        custom.mkdir()
        monkeypatch.setenv('CODEX_HOME', str(custom))
        homes = list(parsers._candidate_homes(['CODEX_HOME'], '~/.codex'))
        assert str(custom) in homes                    # 环境变量优先
        assert homes.index(str(custom)) == 0

    def test_discover_env_codex(self, monkeypatch, tmp_path):
        # CODEX_HOME 指向自定义目录 → discover 应自动找到其中的 state_*.sqlite
        import os
        import sqlite3
        home = tmp_path / 'codex_home'
        home.mkdir()
        db = home / 'state_7.sqlite'
        conn = sqlite3.connect(str(db))
        conn.execute('CREATE TABLE threads (id TEXT)')
        conn.commit()
        conn.close()
        monkeypatch.setenv('CODEX_HOME', str(home))
        found = [s for s in parsers.discover() if s['type'] == 'codex']
        assert any(os.path.normpath(s['path']) == os.path.normpath(str(db)) for s in found)

    def test_rollout_hourly_extracts_model(self, tmp_path):
        # world_state 事件里的 state.model 应被提取，作为每会话真实模型
        r = tmp_path / 'r.jsonl'
        r.write_text(
            json.dumps({"type": "world_state",
                        "payload": {"state": {"model": "gpt-5.6-sol"}}}) + "\n"
            + json.dumps({"timestamp": "2026-01-01T10:00:00.000Z", "type": "event_msg",
                          "payload": {"type": "token_count",
                                     "info": {"total_token_usage": {"input_tokens": 10}}}}) + "\n",
            encoding="utf-8")
        buckets, meta = parsers._codex_rollout_hourly(str(r))
        assert meta["model"] == "gpt-5.6-sol"
        assert sum(b["input"] for b in buckets.values()) == 10

    def test_rollout_hourly_no_model(self, tmp_path):
        # 无 world_state 时 model 为 None（parse_codex 兜底 default_model）
        r = tmp_path / 'r.jsonl'
        r.write_text(
            json.dumps({"timestamp": "2026-01-01T10:00:00.000Z", "type": "event_msg",
                        "payload": {"type": "token_count",
                                   "info": {"total_token_usage": {"input_tokens": 5}}}}) + "\n",
            encoding="utf-8")
        buckets, meta = parsers._codex_rollout_hourly(str(r))
        assert meta["model"] is None
        assert sum(b["input"] for b in buckets.values()) == 5

    def test_rollout_start_from_filename(self, tmp_path):
        # rollout 文件名时间（本地时区）解析为会话兜底开始时间
        ts = parsers._codex_rollout_start(
            r"D:\Users\x\.codex\sessions\2026\08\14\rollout-2026-08-14T09-32-30-abc.jsonl")
        from datetime import datetime
        assert datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") == "2026-08-14 09:32"
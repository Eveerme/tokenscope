"""四工具解析器单元测试（mock 数据）"""
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
        by_id = {r["id"]: r for r in recs}

        r1 = by_id["t1"]
        assert r1["model"] == "gpt-test"    # 显式传入，不依赖本机 config.toml
        assert r1["source"] == "desktop"    # Codex Desktop -> desktop
        assert r1["input"] == 100
        assert r1["output"] == 20
        assert r1["cache_read"] == 60
        assert r1["reasoning"] == 5
        assert r1["api_calls"] == 1
        assert r1["cwd"] == "/home/u/p1"
        # created_at 是秒时间戳（10 位）直接使用
        assert abs(r1["started_at"] - 1700000000.0) < 1

    def test_zero_usage_keeps_source(self, codex_db):
        recs = {r["id"]: r for r in parsers.parse_codex(str(codex_db), default_model="gpt-test")}
        r2 = recs["t2"]
        assert r2["input"] == 0
        assert r2["output"] == 0
        assert r2["source"] == "desktop"    # 0 消耗会话仍按 originator 归源

    def test_cli_originator(self, codex_db):
        recs = {r["id"]: r for r in parsers.parse_codex(str(codex_db), default_model="gpt-test")}
        assert recs["t3"]["source"] == "cli"    # Codex CLI -> cli


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

    def test_user_only_session(self, claude_projects):
        recs = {r["title"]: r for r in parsers.parse_claude(str(claude_projects))}
        r = recs["hi"]
        assert r["api_calls"] == 0
        assert r["input"] == 0


class TestZcode:
    def test_aggregate(self, zcode_db):
        recs = parsers.parse_zcode(str(zcode_db))
        assert len(recs) == 2
        by_id = {r["id"]: r for r in recs}
        z1 = by_id["z1"]
        # 只统计 status=completed：GLM-X 两次 + GLM-Y rate_limited 排除
        assert z1["input"] == 300        # 100 + 200
        assert z1["output"] == 30
        assert z1["reasoning"] == 5
        assert z1["cache_write"] == 50   # cache_creation
        assert z1["cache_read"] == 120   # 20 + 100
        assert z1["api_calls"] == 2
        assert z1["model"] == "GLM-X"    # 用量最大的模型
        assert z1["title"] == "zcode 会话"
        assert abs(z1["started_at"] - 1700000000.0) < 1

    def test_other_session(self, zcode_db):
        recs = {r["id"]: r for r in parsers.parse_zcode(str(zcode_db))}
        assert recs["z2"]["input"] == 10
        assert recs["z2"]["api_calls"] == 1


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

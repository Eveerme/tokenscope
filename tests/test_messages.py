"""会话对话消息提取（导出 MD）单元测试"""
import json

import parsers
import server


def _src(type_, path):
    return {"type": type_, "name": type_, "path": str(path), "auto": False}


class TestHermesMessages:
    def test_extract_and_filter_system(self, hermes_db):
        msgs = parsers._hermes_messages(str(hermes_db), "s1")
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "tool"]  # system 被过滤
        assert msgs[0]["content"] == "你好"
        assert msgs[1]["reasoning"] == "思考中"
        assert msgs[2]["tool"] == "terminal"

    def test_no_session(self, hermes_db):
        assert parsers._hermes_messages(str(hermes_db), "不存在") == []


class TestCodexMessages:
    def test_extract_and_filter_developer(self, codex_db):
        msgs = parsers._codex_messages(str(codex_db), "t1")
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant"]  # developer 被过滤
        assert msgs[0]["content"] == "帮我写代码"
        assert msgs[1]["content"] == "好的"

    def test_missing_thread(self, codex_db):
        assert parsers._codex_messages(str(codex_db), "不存在") == []


class TestClaudeMessages:
    def test_extract(self, claude_projects):
        msgs = parsers._claude_messages(str(claude_projects), "sess1")
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "assistant"]
        assert msgs[0]["content"] == "你好"
        assert msgs[1]["content"] == "你好，有什么可以帮你？"


class TestZcodeMessages:
    def test_extract_and_sort(self, zcode_db):
        msgs = parsers._zcode_messages(str(zcode_db), "z1")
        # step-start part 被过滤；按 sequence 排序
        assert [(m["role"], m["content"]) for m in msgs] == [("user", "你好"), ("assistant", "你好！")]


class TestRenderMd:
    def test_render(self):
        rec = {
            "tool": "hermes", "title": "测试会话", "model": "gpt-test", "source": "desktop",
            "id": "s1", "cwd": "/home/u/p1", "started_at": 1700000000, "ended_at": 1700000100,
            "input": 1000, "output": 500, "cache_read": 2000, "cache_write": 0,
            "reasoning": 100, "api_calls": 3,
        }
        msgs = [
            {"role": "user", "content": "你好", "reasoning": "", "tool": "", "ts": 1},
            {"role": "assistant", "content": "你好！", "reasoning": "思考", "tool": "", "ts": 2},
        ]
        md = server._render_session_md(rec, msgs)
        assert md.startswith("# 测试会话")
        assert "**工具**：Hermes" in md
        assert "## 对话内容" in md
        assert "### 👤 用户" in md
        assert "### 🤖 助手" in md
        assert "你好！" in md
        assert "<details>" in md and "💭 思考过程" in md

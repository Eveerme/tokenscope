"""server 聚合层单元测试（用 mock 数据源构造 cfg，不触发自动发现）"""
import server
import parsers


def _cfg(sources):
    return {"sources": [{"type": t, "name": t, "path": str(p), "auto": False}
                        for t, p in sources],
            "pricing": {"gpt-test": {"input": 1.0, "output": 2.0, "cache_read": 0.1,
                                     "cache_write": 0.5}}}


class TestAggregation:
    def test_summary_totals(self, hermes_db, codex_db, claude_projects, zcode_db):
        cfg = _cfg([("hermes", hermes_db), ("codex", codex_db),
                    ("claude", claude_projects), ("zcode", zcode_db)])
        summ = server.api_summary(cfg, {})
        t = summ["totals"]
        # hermes 2 + codex 3 + claude 2 + zcode 2
        assert t["sessions"] == 9
        # input: hermes 1100 + codex 110 + claude 15 + zcode 310
        assert t["input"] == 1100 + 110 + 15 + 310
        assert t["output"] == 550 + 25 + 22 + 31
        # by_tool 四个工具齐全
        assert {x["key"] for x in summ["by_tool"]} == {"hermes", "codex", "claude", "zcode"}

    def test_tool_filter(self, hermes_db, codex_db):
        cfg = _cfg([("hermes", hermes_db), ("codex", codex_db)])
        summ = server.api_summary(cfg, {"tool": ["codex"]})
        assert summ["totals"]["sessions"] == 3
        assert [x["key"] for x in summ["by_tool"]] == ["codex"]

    def test_time_filter(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        # s1 在 1700000000 开始，s2 在 1700000200；窗口 [1700000000, 1700000200) 只含 s1
        summ = server.api_summary(cfg, {"from": ["1700000000"], "to": ["1700000200"]})
        assert summ["totals"]["sessions"] == 1
        assert summ["totals"]["input"] == 1000

    def test_cost_estimate(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        summ = server.api_summary(cfg, {})
        t = summ["totals"]
        # gpt-test 定价：input 1000/1e6*1 + output 500/1e6*2 + cache 2000/1e6*0.1
        # s1 = 0.001 + 0.001 + 0.0002 = 0.0022；s2 = 0.0001 + 0.0001 = 0.0002
        assert t["cost"] is not None
        assert abs(t["cost"] - 0.0024) < 1e-6

    def test_prev_totals(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        # 有限窗口有上一周期；全量窗口 prev 为 None
        summ = server.api_summary(cfg, {"from": ["1700000000"], "to": ["1700000200"]})
        prev = summ["prev_totals"]
        assert prev is not None
        assert prev["sessions"] == 0          # 窗口前没有数据
        summ_all = server.api_summary(cfg, {})
        assert summ_all["prev_totals"] is None

    def test_timeline_hour(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        tl = server.api_timeline(cfg, {"from": ["1700000000"], "to": ["1700000200"],
                                       "granularity": ["hour"]})
        assert tl["granularity"] == "hour"
        assert len(tl["points"]) == 1
        assert tl["points"][0]["date"].endswith(":00")
        assert tl["points"][0]["input"] == 1000

    def test_by_project_merge(self, hermes_db):
        import os
        cfg = _cfg([("hermes", hermes_db)])
        summ = server.api_summary(cfg, {})
        projects = {p["key"]: p for p in summ["by_project"]}
        expected = {os.path.normpath("/home/user/proj1"), os.path.normpath("/home/user/proj2")}
        assert set(projects.keys()) == expected

    def test_by_project_windows_case_fold(self):
        # Windows 归一化：大小写 + 反斜杠折叠
        import os
        if os.name == "nt":
            assert server._project_key(r"D:\Work\AI") == server._project_key(r"d:\work\ai")
            assert server._project_key(r"D:\\work\\AI") == server._project_key(r"D:\work\AI")
        else:
            assert server._project_key("/a/b") == server._project_key("/a/b")


class TestCSV:
    def test_export(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        csv = server.api_export_csv(cfg, {})
        lines = csv.strip().split("\n")
        assert len(lines) == 3                       # 表头 + 2 会话（按时间倒序）
        assert lines[0].startswith("工具,标题,模型")
        assert "测试会话" in csv
        assert "1000" in csv                         # input tokens

    def test_export_filter(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        csv = server.api_export_csv(cfg, {"tool": ["hermes"], "model": ["gpt-test"]})
        assert len(csv.strip().split("\n")) == 3
        csv2 = server.api_export_csv(cfg, {"model": ["nope"]})
        assert len(csv2.strip().split("\n")) == 1   # 只有表头


class TestSessionsAPI:
    def test_sessions_page(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        res = server.api_sessions(cfg, {"page": ["1"], "page_size": ["10"]})
        assert res["total"] == 2
        assert len(res["items"]) == 2
        by_id = {i["id"]: i for i in res["items"]}
        row = by_id["s1"]
        assert row["tool"] == "hermes"
        assert row["source_label"] == "桌面端"
        assert row["cost"] is not None              # gpt-test 有定价
        assert by_id["s2"]["source_label"] == "终端"

    def test_sessions_search(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        res = server.api_sessions(cfg, {"q": ["测试"]})
        assert res["total"] == 1
        assert res["items"][0]["id"] == "s1"

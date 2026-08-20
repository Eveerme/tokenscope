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
        # hermes 2 + codex 3 + claude 1（纯 user 会话被跳过）+ zcode 2
        assert t["sessions"] == 8
        # input: hermes 1100 + codex 110 + claude 15 + zcode 140（归一化后 130+10）
        assert t["input"] == 1100 + 110 + 15 + 140
        # output: hermes 550 + codex 25 + claude 22 + zcode 26（归一化后 25+1）
        assert t["output"] == 550 + 25 + 22 + 26
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
        from datetime import datetime
        cfg = _cfg([("hermes", hermes_db)])
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tl = server.api_timeline(cfg, {"from": [str(day_start.timestamp())],
                                       "granularity": ["hour"]})
        assert tl["granularity"] == "hour"
        pts = tl["points"]
        # 从当天 0 点起，每小时一桶，连续到当前小时（缺失补 0）
        assert pts[0]["date"] == day_start.strftime("%Y-%m-%d %H:00")
        now = datetime.now()
        assert pts[-1]["date"] == now.strftime("%Y-%m-%d %H:00")
        assert len(pts) == now.hour + 1
        # hermes_db 会话是 2023 年，被今天时间过滤，所有桶为 0
        assert all(p["input"] == 0 for p in pts)

    def test_timeline_day(self, hermes_db):
        cfg = _cfg([("hermes", hermes_db)])
        tl = server.api_timeline(cfg, {"granularity": ["day"]})
        assert tl["granularity"] == "day"
        pts = tl["points"]
        assert len(pts) == 1
        assert pts[0]["input"] == 1100  # s1(1000) + s2(100)

    def test_resolve_db_path(self, tmp_path):
        # codex 目录 → state_*.sqlite（取版本最大）
        cx = tmp_path / 'codex'
        cx.mkdir()
        (cx / 'state_1.sqlite').write_text('')
        (cx / 'state_5.sqlite').write_text('')
        assert server._resolve_db_path(str(cx), 'codex').endswith('state_5.sqlite')
        # zcode 目录 → cli/db/db.sqlite
        zc = tmp_path / 'zcode'
        (zc / 'cli' / 'db').mkdir(parents=True)
        (zc / 'cli' / 'db' / 'db.sqlite').write_text('')
        assert server._resolve_db_path(str(zc), 'zcode').endswith('db.sqlite')
        # hermes 目录 → state.db
        hm = tmp_path / 'hermes'
        hm.mkdir()
        (hm / 'state.db').write_text('')
        assert server._resolve_db_path(str(hm), 'hermes').endswith('state.db')
        # 文件路径直通
        f = tmp_path / 'x.sqlite'
        f.write_text('')
        assert server._resolve_db_path(str(f), 'codex') == str(f)
        # 空目录 → None
        empty = tmp_path / 'empty'
        empty.mkdir()
        assert server._resolve_db_path(str(empty), 'codex') is None

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


def test_load_pricing_json():
    import os
    p = server._load_pricing_json()
    if os.path.isfile(os.path.join(server.BASE_DIR, "pricing.json")):
        # pricing.json 入库后，CI/本地均应有 56 个模型（以 model-pricing.json 为准）
        assert len(p) >= 50
        assert p["gpt-5.6-sol"]["input"] == 5.0
        assert p["deepseek-v4-pro"]["output"] == 0.87
        assert p["gpt-5.5-pro"]["input"] == 30.0
    else:
        assert p == {}


class TestPricingUpdate:
    """models.dev 定价更新（网络层用 monkeypatch 打桩，不依赖联网）"""

    def test_official_provider(self):
        assert server._official_provider("deepseek-chat") == "deepseek"
        assert server._official_provider("gpt-4o") == "openai"
        assert server._official_provider("o3-mini") == "openai"
        assert server._official_provider("claude-sonnet-4-5") == "anthropic"
        assert server._official_provider("qwen3-max") == "alibaba"
        assert server._official_provider("glm-4.6") == "zhipuai"
        assert server._official_provider("unknown-xyz") is None

    def test_normalize_model_id(self):
        assert server._normalize_model_id("openai/gpt-4o") == "gpt-4o"
        assert server._normalize_model_id("DeepSeek-Chat") == "deepseek-chat"
        assert server._normalize_model_id("  gpt-4o  ") == "gpt-4o"
        assert server._normalize_model_id("") == ""

    def test_refresh_updates_and_preserves(self, tmp_path, monkeypatch):
        # 打桩网络层：返回固定的 models.dev 定价
        fake_lookup = {
            "deepseek-chat": {"input": 0.14, "output": 0.28,
                              "cache_read": 0.0028, "cache_write": 0.0},
            "gpt-4o": {"input": 2.5, "output": 10.0,
                       "cache_read": 1.25, "cache_write": 0.0},
        }
        monkeypatch.setattr(server, "fetch_models_dev_pricing",
                            lambda timeout=15: fake_lookup)
        monkeypatch.setattr(server, "CONFIG_PATH", str(tmp_path / "config.json"))
        cfg = {"sources": [], "pricing": {
            "deepseek-chat": {"input": 0.10, "output": 0.30,
                              "cache_read": 0.01, "cache_write": 0.0},
            "gpt-4o": {"input": 2.0, "output": 8.0,
                       "cache_read": 1.0, "cache_write": 0.0},
            "my-custom-model": {"input": 9.9, "output": 9.9,
                                "cache_read": 0.0, "cache_write": 0.0},
            "openai/gpt-4o": {"input": 1.0, "output": 9.0,
                              "cache_read": 0.1, "cache_write": 0.0},
        }}
        stats = server.refresh_pricing_from_models_dev(cfg)
        # deepseek-chat / gpt-4o / openai/gpt-4o 命中 models.dev（3 个），
        # my-custom-model 未收录保留（1 个）
        assert stats == {"updated": 3, "preserved": 1, "total": 4}
        assert cfg["pricing"]["deepseek-chat"]["input"] == 0.14
        assert cfg["pricing"]["gpt-4o"]["input"] == 2.5
        # provider 前缀归一化后命中 gpt-4o
        assert cfg["pricing"]["openai/gpt-4o"]["input"] == 2.5
        # 未收录模型保留原值
        assert cfg["pricing"]["my-custom-model"]["input"] == 9.9
        # 配置已落盘
        import os
        assert os.path.isfile(str(tmp_path / "config.json"))

    def test_refresh_failure_raises(self, monkeypatch):
        def _boom(timeout=15):
            raise OSError("network unreachable")
        monkeypatch.setattr(server, "fetch_models_dev_pricing", _boom)
        cfg = {"sources": [], "pricing": {"deepseek-chat": {"input": 1}}}
        try:
            server.refresh_pricing_from_models_dev(cfg)
            assert False, "should have raised"
        except OSError:
            pass
        # 失败时不改动定价
        assert cfg["pricing"]["deepseek-chat"]["input"] == 1


class TestSourcesFull:
    def test_dir_based_source_exists(self, dsh_sessions):
        """目录型数据源（dsh/claude）应按 isdir 判定存在，而非 isfile"""
        cfg = _cfg([("dsh", dsh_sessions)])
        res = server.api_sources_full(cfg)
        dsh = [s for s in res["sources"] if s["type"] == "dsh"][0]
        assert dsh["exists"] is True          # 目录存在 → exists=True
        assert dsh["db_sessions"] > 0         # 能解析出会话
        assert dsh["size"] == 0               # 目录型无文件大小
        assert dsh["modified_at"] == 0        # 目录型不取 mtime

    def test_file_based_source_missing(self, tmp_path):
        """文件型数据源路径不存在时 exists=False"""
        cfg = _cfg([("hermes", tmp_path / "nope" / "state.db")])
        res = server.api_sources_full(cfg)
        hermes = [s for s in res["sources"] if s["type"] == "hermes"][0]
        assert hermes["exists"] is False

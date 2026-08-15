
import server

def _cfg(sources):
    return {"sources": [{"type": t, "name": t, "path": str(p), "auto": False}
                        for t, p in sources],
            "pricing": {"gpt-test": {"input": 1.0, "output": 2.0, "cache_read": 0.1,
                                     "cache_write": 0.5}}}

def test_cache_savings(hermes_db):
    cfg = _cfg([("hermes", hermes_db)])
    summ = server.api_summary(cfg, {})
    t = summ["totals"]
    # s1 cache_read=2000: 2000/1e6 * (input 1.0 - cache_read 0.1) = 0.0018
    assert abs(t["cache_savings"] - 0.0018) < 1e-9

def test_cache_savings_prev(hermes_db):
    cfg = _cfg([("hermes", hermes_db)])
    summ = server.api_summary(cfg, {"from": ["1700000000"], "to": ["1700000200"]})
    pv = summ["prev_totals"]
    assert pv is not None and "cache_savings" in pv
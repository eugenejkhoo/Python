from zdte.ibkr_check import format_report


def test_format_report_fatal():
    out = format_report({"fatal": "could not connect"})
    assert "FAIL  could not connect" in out


def test_format_report_full():
    r = {
        "host": "127.0.0.1", "port": 4002, "client_id": 7, "server_version": 176, "server_time": "t",
        "accounts": ["DU123"], "paper": True,
        "summary": {"NetLiquidation": "50000", "BuyingPower": "200000", "AvailableFunds": "50000"},
        "errors": [(1, 2104, "ok"), (2, 10089, "subscription required")],
        "symbols": {
            "SPY": {"conId": 1, "stock_quote": {"bid": 570.1, "ask": 570.2, "last": 570.15, "delayed": False},
                    "bars_today": 120, "last_bar": "x", "next_expiry": "20261002", "zero_dte_today": True,
                    "atm_call": {"symbol": "SPY 261002C00570000", "strike": 570.0, "bid": float("nan"), "ask": float("nan"), "delayed": True}},
            "QQQ": {"error": "qualify failed"},
        },
    }
    out = format_report(r)
    assert "paper account" in out
    assert "PASS  stock quote bid 570.1" in out
    assert "FAIL  ATM call" in out and "OPRA" in out
    assert "FAIL  qualify failed" in out
    assert "[10089]" in out and "[2104]" not in out

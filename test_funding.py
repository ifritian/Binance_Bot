import funding_radar as fr


def test_parse_and_pick():
    payload = [{"symbol": "DOGEUSDT", "lastFundingRate": "0.0006"}, {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"},
               {"symbol": "XRPUSDT", "lastFundingRate": "-0.0003"}, {"symbol": "FOOUSDT", "lastFundingRate": "0.01"}]
    r = fr.parse_binance(payload, {"DOGE", "BTC", "XRP"})
    assert set(r) == {"DOGE", "BTC", "XRP"}
    sel = fr.pick(r, {"DOGE": 3.0})
    assert [t for t, *_ in sel["longs"]] == ["DOGE"] and [t for t, *_ in sel["shorts"]] == ["XRP"]
    assert fr.pick({"BTC": 0.0001}, {}) is None


def test_text_limits_cashtags():
    sel = fr.pick({"A": 0.001, "B": 0.0009, "C": 0.0008, "D": -0.0004, "E": -0.0003}, {})
    t = fr.build_text(sel)
    assert t.count("$") <= 3 and "−0.040%" in t


def test_fetch_falls_back_to_okx():
    class R:
        def __init__(self, data, bad=False): self.data, self.bad = data, bad
        def raise_for_status(self):
            if self.bad: raise fr.requests.RequestException("451")
        def json(self): return self.data
    def get(url, **kw):
        if "binance" in url: return R({}, bad=True)
        return R({"data": [{"fundingRate": "0.0005"}]})
    out = fr.fetch({"BTC", "ETH"}, get=get)
    assert out == {"BTC": 0.0005, "ETH": 0.0005}


def test_previous_line():
    line = fr.previous_line({"ticker": "DOGE", "funding": 0.0006, "price": 100}, 95)
    assert "crowded longs" in line and "−5.0%" in line

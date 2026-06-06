from pathlib import Path
from scripts.build.matrix import expand

def test_expand_win11_24h2_includes_pro_and_enterprise():
    rows = expand(Path("config/editions.yaml"))
    pro = [r for r in rows if r["product"] == "win11-24h2" and r["edition"] == "professional"]
    ent = [r for r in rows if r["product"] == "win11-24h2" and r["edition"] == "enterprise"]
    assert pro and ent

def test_expand_includes_ltsc_for_25h2():
    rows = expand(Path("config/editions.yaml"))
    ltsc = [r for r in rows if r["product"] == "win11-25h2" and r["edition"] == "iotenterprise"]
    assert ltsc
    assert "LTSC" in ltsc[0]["label"]

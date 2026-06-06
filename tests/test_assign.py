import pytest
from scripts.rclone.assign import assign, Account

def make_accounts():
    return [
        Account(name="a1", handles_products=["win11-24h2"], quota_gb=15, used_gb=0),
        Account(name="a2", handles_products=["win11-24h2", "win10-22h2"], quota_gb=15, used_gb=0),
    ]

def test_picks_account_handling_product():
    a = assign("win11-24h2", 5.0, make_accounts())
    assert a in ("a1", "a2")

def test_rejects_when_no_account_handles_product():
    with pytest.raises(RuntimeError):
        assign("win11-some-future", 5.0, make_accounts())

def test_skips_account_that_cannot_fit_iso():
    accounts = [
        Account(name="full", handles_products=["win11-24h2"], quota_gb=15, used_gb=14.0),
        Account(name="ok",   handles_products=["win11-24h2"], quota_gb=15, used_gb=0.0),
    ]
    assert assign("win11-24h2", 5.0, accounts) == "ok"

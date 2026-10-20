import json

from scripts import meituan_probe
from app.meituan_public import PublicPageError


def test_cli_outputs_product_and_origin_metadata(capsys, monkeypatch):
    monkeypatch.setattr(meituan_probe, "fetch_public_product", lambda _: {
        "product_name": "测试团购券",
        "display_price_cents": 1880,
        "final_price_verified": False,
        "source_type": "public_page",
    })
    assert meituan_probe.main(["https://i.meituan.com/deal/x"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["display_price_cents"] == 1880
    assert payload["final_price_verified"] is False


def test_cli_fails_closed_and_prints_reason(capsys, monkeypatch):
    def fail(_):
        raise PublicPageError("access_restricted", "网页拒绝访问", 403)
    monkeypatch.setattr(meituan_probe, "fetch_public_product", fail)
    assert meituan_probe.main(["https://i.meituan.com/deal/x"]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["reason"] == "access_restricted"

"""Signed partner submission tests: authentication, replay, strict matching and safety."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.main import create_app
from app import store


SECRET = "test-strong-secret-" + "a" * 48


def setup_catalog(client):
    headers = {"X-Admin-Token": "admin-secret"}
    s = client.post("/api/admin/stores", headers=headers, json={
        "name": "测试奶茶店", "city": "上海", "district": "黄浦区",
        "address": "上海市黄浦区测试路12号",
    })
    assert s.status_code == 201, s.text
    store_id = s.json()["id"]
    p = client.post("/api/admin/products", headers=headers, json={
        "store_id": store_id, "name": "双人套餐",
        "details": "奶茶×2 + 蛋糕×1", "usage_terms": "周一至周五可用；不可预约",
    })
    assert p.status_code == 201, p.text
    return store_id, p.json()["id"]


def valid_payload(store_id, product_id, *, event="event_abcdefgh", platform="meituan"):
    now = datetime.now(timezone.utc)
    domain = {"meituan": "www.meituan.com", "douyin": "www.douyin.com",
              "kuaishou": "www.kuaishou.com", "dianping": "www.dianping.com"}[platform]
    return {
        "event_id": event, "store_id": store_id, "product_id": product_id,
        "platform": platform, "product_name": "双人套餐",
        "details": "奶茶×2 + 蛋糕×1",
        "usage_terms": "周一至周五可用；不可预约",
        "store_address": "上海市黄浦区测试路12号",
        "list_price_cents": 4000,
        "discount_cents": 600 if platform == "meituan" else 300,
        "fee_cents": 0, "availability": "available", "eligibility": "public",
        "quoted_at": now.isoformat(),
        "valid_until": (now + timedelta(minutes=25)).isoformat(),
        "source_url": "https://" + domain + "/deal/test",
        "note": "合作方测试价格，不代表官方接口",
    }


def signed_post(client, payload, secret=SECRET, *, tamper=False, timestamp=None):
    ts = str(int(time.time())) if timestamp is None else str(timestamp)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    sig = hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    if tamper:
        sig = "0" * 64
    return client.post("/api/partner/quotes", content=raw, headers={
        "Content-Type": "application/json",
        "X-Partner-Id": "merchant_alpha",
        "X-Timestamp": ts,
        "X-Signature": "sha256=" + sig,
    })


def configured(monkeypatch):
    monkeypatch.setenv("TBB_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setenv("TBB_PARTNER_KEYS_JSON", json.dumps({"merchant_alpha": SECRET}))


def test_unconfigured_partner_ingestion_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("TBB_PARTNER_KEYS_JSON", raising=False)
    with TestClient(create_app(str(tmp_path / "no-feed.db"), seed_demo=False)) as client:
        status = client.get("/api/integrations/status").json()
        assert status["signed_partner_feed"] == "disabled"
        assert all(x == "not_connected" for x in status["official_platform_apis"].values())
        assert signed_post(client, {}).status_code == 503


def test_signed_two_platform_prices(tmp_path, monkeypatch):
    configured(monkeypatch)
    with TestClient(create_app(str(tmp_path / "feed.db"), seed_demo=False)) as client:
        sid, pid = setup_catalog(client)
        status = client.get("/api/integrations/status").json()
        assert status["signed_partner_feed"] == "configured"
        assert status["official_platform_apis"]["meituan"] == "not_connected"
        meituan = valid_payload(sid, pid)
        douyin = valid_payload(sid, pid, event="event_ijklmnop", platform="douyin")
        assert signed_post(client, meituan).status_code == 201
        assert signed_post(client, douyin).status_code == 201
        result = client.get(f"/api/stores/{sid}/comparison").json()["groups"][0]
        assert result["real_comparison_ready"]
        assert result["comparison_basis"] == "partner_reported"
        assert result["lowest_real_cents"] == 3400
        assert result["possible_saving_cents"] == 300
        assert all(q["source_type"] == "partner_feed" for q in result["quotes"])
        assert all(q["comparison_eligible"] for q in result["quotes"])
        # Exact same signed event cannot be replayed into historical records.
        assert signed_post(client, meituan).status_code == 409
        with store.connect(str(tmp_path / "feed.db")) as db:
            assert db.execute("SELECT COUNT(*) FROM partner_quotes").fetchone()[0] == 2


def test_tampering_expired_auth_and_oversized_payload(tmp_path, monkeypatch):
    configured(monkeypatch)
    with TestClient(create_app(str(tmp_path / "auth.db"), seed_demo=False)) as client:
        sid, pid = setup_catalog(client)
        payload = valid_payload(sid, pid)
        assert signed_post(client, payload, tamper=True).status_code == 401
        assert signed_post(client, payload, timestamp=int(time.time()) - 400).status_code == 401
        assert signed_post(client, payload, secret="wrong-secret-with-enough-characters").status_code == 401
        assert client.post("/api/partner/quotes", content=b"x" * 33000).status_code == 413
        with store.connect(str(tmp_path / "auth.db")) as db:
            assert db.execute("SELECT COUNT(*) FROM partner_quotes").fetchone()[0] == 0


def test_identity_price_url_and_time_must_be_valid(tmp_path, monkeypatch):
    configured(monkeypatch)
    with TestClient(create_app(str(tmp_path / "mismatch.db"), seed_demo=False)) as client:
        sid, pid = setup_catalog(client)
        base = valid_payload(sid, pid)
        now = datetime.now(timezone.utc)
        invalid = [
            {"store_address": "上海市黄浦区另外一家门店"},
            {"details": "奶茶×1 + 蛋糕×1"},
            {"usage_terms": "周末通用"},
            {"product_name": "单人套餐"},
            {"store_id": sid + 1},
            {"source_url": "https://evil-meituan.com/deal"},
            {"source_url": "http://www.meituan.com/deal"},
            {"discount_cents": 4001},
            {"quoted_at": (now - timedelta(minutes=12)).isoformat()},
            {"valid_until": (now - timedelta(seconds=1)).isoformat()},
            {"eligibility": "invalid"},
            {"unexpected_field": "no"},
        ]
        for changes in invalid:
            response = signed_post(client, {**base, **changes})
            assert response.status_code == 422, (changes, response.text)
        # All failed validation, and no partial rows may have been inserted.
        with store.connect(str(tmp_path / "mismatch.db")) as db:
            assert db.execute("SELECT COUNT(*) FROM partner_quotes").fetchone()[0] == 0


def test_sold_out_partner_feed_never_produces_savings(tmp_path, monkeypatch):
    configured(monkeypatch)
    with TestClient(create_app(str(tmp_path / "unavailable.db"), seed_demo=False)) as client:
        sid, pid = setup_catalog(client)
        ok = valid_payload(sid, pid)
        unavailable = valid_payload(sid, pid, event="event_12345678", platform="douyin")
        unavailable["availability"] = "sold_out"
        assert signed_post(client, ok).status_code == 201
        assert signed_post(client, unavailable).status_code == 201
        result = client.get(f"/api/stores/{sid}/comparison").json()["groups"][0]
        assert not result["real_comparison_ready"]
        assert result["possible_saving_cents"] is None
        assert any(not q["comparison_eligible"] for q in result["quotes"])


def test_existing_demo_db_keeps_working_and_feed_wins(tmp_path, monkeypatch):
    configured(monkeypatch)
    path = str(tmp_path / "legacy.db")
    with TestClient(create_app(path, seed_demo=True)) as client:
        comp = client.get("/api/stores/1/comparison").json()
        assert comp["groups"][0]["contains_demo"]
    # Additive schema; launching a second time does not lose old rows.
    with TestClient(create_app(path, seed_demo=True)) as client:
        comp = client.get("/api/stores/1/comparison").json()
        assert comp["groups"][0]["quotes"]
        assert not comp["groups"][0]["real_comparison_ready"]

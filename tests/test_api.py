from fastapi.testclient import TestClient
from app.main import create_app


def test_demo_search_and_comparison(tmp_path):
    app = create_app(str(tmp_path / "test.db"), seed_demo=True)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"]
        assert "上海" in client.get("/api/meta").json()["cities"]
        results = client.get("/api/stores", params={"city": "上海", "q": "咖啡"}).json()["stores"]
        assert len(results) == 1
        store_id = results[0]["id"]
        comp = client.get("/api/stores/" + str(store_id) + "/comparison").json()
        assert comp["groups"][0]["contains_demo"]
        assert comp["groups"][0]["possible_saving_cents"] is None
        assert client.get("/api/stores/999/comparison").status_code == 404
        assert client.get("/api/stores", params={"q": "%"}).json()["stores"] == []


def test_admin_token_and_manual_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("TBB_ADMIN_TOKEN", "test-secret")
    app = create_app(str(tmp_path / "empty.db"), seed_demo=False)
    with TestClient(app) as client:
        token = {"X-Admin-Token": "test-secret"}
        assert client.post("/api/admin/stores", json={}).status_code == 401
        s = client.post("/api/admin/stores", headers=token, json={
            "name": "测试门店", "city": "上海", "district": "徐汇区", "address": "上海测试路1号"
        })
        assert s.status_code == 201
        p = client.post("/api/admin/products", headers=token, json={
            "store_id": s.json()["id"], "name": "单人套餐", "details": "咖啡×1", "usage_terms": "工作日可用"
        })
        assert p.status_code == 201
        base = {"product_id": p.json()["id"], "list_price_cents": 2000,
                "discount_cents": 300, "fee_cents": 0, "note": "管理员手动录入"}
        bad = client.post("/api/admin/quotes", headers=token, json={
            **base, "platform": "meituan", "source_url": "https://evil-meituan.com/deals"
        })
        assert bad.status_code == 422
        a = client.post("/api/admin/quotes", headers=token, json={
            **base, "platform": "meituan", "source_url": "https://www.meituan.com/example"
        })
        b = client.post("/api/admin/quotes", headers=token, json={
            **base, "platform": "douyin", "list_price_cents": 1800, "discount_cents": 300
        })
        assert a.status_code == 201 and b.status_code == 201
        groups = client.get("/api/stores/" + str(s.json()["id"]) + "/comparison").json()["groups"]
        assert groups[0]["real_comparison_ready"]
        assert groups[0]["possible_saving_cents"] == 200
        assert groups[0]["lowest_real_cents"] == 1500


def test_disable_admin_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("TBB_ADMIN_TOKEN", raising=False)
    with TestClient(create_app(str(tmp_path / "no-admin.db"), seed_demo=False)) as client:
        assert client.post("/api/admin/quotes", json={}).status_code == 503


def test_demo_seed_is_idempotent(tmp_path):
    db = str(tmp_path / "same.db")
    for _ in range(2):
        with TestClient(create_app(db, seed_demo=True)) as client:
            assert len(client.get("/api/stores").json()["stores"]) == 3


def test_homepage_renders_interactive_search(tmp_path):
    with TestClient(create_app(str(tmp_path / "home.db"), seed_demo=True)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "团比比" in response.text
        assert "searchForm" in response.text
        assert "citySelect" in response.text
        assert "api/meta" in response.text

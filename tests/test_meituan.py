from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.main import create_app
from app.meituan_public import (
    PublicPageError, fetch_public_product, parse_public_html,
    safe_source_url, validate_url,
)
from app.meituan_alliance import AllianceDataError, query_body, normalize_response

URL = "https://i.meituan.com/deal/abc?id=123&utm_source=debug"
HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
"name":"双人烤肉套餐","description":"肉类×2 + 饮品×2",
"brand":{"@type":"Brand","name":"示例烤肉店"},
"offers":{"@type":"Offer","price":"59.90","priceCurrency":"CNY"}}
</script>
</head></html>
"""


def client_with(status_robots=404, html=HTML, status_page=200,
                content_type="text/html", redirect=None, robots_text=""):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(status_robots, text=robots_text)
        headers = {"content-type": content_type}
        if redirect:
            headers["location"] = redirect
        return httpx.Response(status_page, text=html, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler),
                        follow_redirects=False), calls


def test_jsonld_product_price_and_source_redaction():
    with client_with()[0] as client:
        item = fetch_public_product(URL, client=client)
    assert item["product_name"] == "双人烤肉套餐"
    assert item["brand_name"] == "示例烤肉店"
    assert item["display_price_cents"] == 5990
    assert item["source_type"] == "public_page"
    assert item["final_price_verified"] is False
    assert item["price_evidence"] == "json_ld:Product.offers.price"
    assert item["source_url"] == "https://i.meituan.com/deal/abc?id=123"
    assert "utm_source" not in str(item)


@pytest.mark.parametrize("bad", [
    "http://i.meituan.com/deal/123",
    "https://www.meituan.com.evil.example/deal",
    "https://evil.example/?url=meituan.com",
    "https://i.meituan.com@evil.example/deal",
    "https://i.meituan.com:444/deal",
    "file:///etc/passwd",
])
def test_deny_arbitrary_urls(bad):
    with pytest.raises(PublicPageError):
        validate_url(bad)


def test_robots_denial_is_enforced():
    client, calls = client_with(status_robots=200,
                                robots_text="User-agent: *\nDisallow: /deal/\n")
    with client:
        with pytest.raises(PublicPageError) as e:
            fetch_public_product(URL, client=client)
    assert e.value.reason == "robots_disallow"
    assert len(calls) == 1


def test_server_denials_and_redirects_no_follow():
    for status, redirects, reason in [
        (403, None, "access_restricted"),
        (429, None, "access_restricted"),
        (302, "https://example.com/internal", "redirect_blocked"),
    ]:
        client, calls = client_with(status_page=status, redirect=redirects)
        with client:
            with pytest.raises(PublicPageError) as error:
                fetch_public_product(URL, client=client)
        assert error.value.reason == reason
        assert len(calls) == 2
        assert "example.com/internal" not in "".join(calls)


def test_html_content_limit_and_type_check():
    client, _ = client_with(html="x" * (512 * 1024 + 1))
    with client, pytest.raises(PublicPageError) as e:
        fetch_public_product(URL, client=client)
    assert e.value.reason == "too_large"
    client, _ = client_with(content_type="application/json")
    with client, pytest.raises(PublicPageError) as e:
        fetch_public_product(URL, client=client)
    assert e.value.reason == "not_html"


def test_no_price_is_partial_not_fabricated():
    page = """<script type="application/ld+json">
    {"@type":"Product","name":"双人套餐","offers":{}}
    </script>"""
    product = parse_public_html(page, URL)
    assert product["product_name"] == "双人套餐"
    assert product["display_price_cents"] is None
    assert product["price_status"] == "not_disclosed"
    assert product["final_price_verified"] is False


def test_generic_monetary_text_is_not_a_product():
    with pytest.raises(PublicPageError) as e:
        parse_public_html("<title>美团</title><p>¥59.90 团购</p>", URL)
    assert e.value.reason == "no_product_metadata"


def test_meta_product_price_as_fallback():
    html = """<meta property="og:type" content="product">
    <meta property="og:title" content="火锅套餐">
    <meta property="product:price:amount" content="39.90">
    <meta property="product:price:currency" content="CNY">"""
    data = parse_public_html(html, URL)
    assert data["display_price_cents"] == 3990
    assert data["price_evidence"] == "meta:product:price:amount"


def test_wrong_currency_and_precision_not_counted():
    for price, currency in [("59.999", "CNY"), ("40.00", "USD")]:
        html = ("""<script type="application/ld+json">
        {"@type":"Product","name":"双人套餐","offers":{"price":"""
                + json.dumps(price) + ',"priceCurrency":' + json.dumps(currency) + "}}</script>")
        assert parse_public_html(html, URL)["display_price_cents"] is None


def test_alliance_query_search_does_not_conflict_with_business_filters():
    q = query_body(keyword="火锅", city_id="10")
    assert q["searchText"] == "火锅"
    assert "platform" not in q and "bizLine" not in q
    assert q["cityId"] == "10"
    exact = query_body(product_signs=["ABC123"], page_size=5)
    assert exact["platform"] == 2 and exact["bizLine"] == 1
    assert exact["productViewSignList"] == ["ABC123"]
    with pytest.raises(AllianceDataError):
        query_body(keyword="火锅", product_signs=["abc"])


def test_alliance_response_parse_official_schema_money_and_poi_scope():
    response = {
        "code": 0, "hasNext": True, "searchId": "next",
        "data": [
            {"couponPackDetail": {
                "name": "双人火锅套餐", "sellPrice": "59.90",
                "originalPrice": "99.00", "platform": 2, "bizLine": 1,
                "productViewSign": "ABC", "saleStatus": True,
            }, "brandInfo": {"brandName": "示例品牌"},
               "availablePoiInfo": {"availablePoiNum": 12}},
            {"couponPackDetail": {
                "name": "外卖套餐", "sellPrice": "8.00",
                "platform": 1, "saleStatus": True}},
            {"couponPackDetail": {
                "name": "已下架", "sellPrice": "59.90", "saleStatus": False}},
        ],
    }
    result = normalize_response(response)
    assert result["skipped"] == 2
    assert result["has_next"] and result["search_id"] == "next"
    item = result["products"][0]
    assert item["display_price_cents"] == 5990
    assert item["original_price_cents"] == 9900
    assert item["business_scope"] == "confirmed_dining"
    assert item["available_poi_count"] == 12
    assert item["merchant_poi_unresolved"] is True
    with pytest.raises(AllianceDataError):
        normalize_response({"code": 400, "message": "unauthorized"})


def test_admin_collect_is_independent_of_real_quote_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("TBB_ADMIN_TOKEN", "fixture-admin-token")
    monkeypatch.setattr("app.meituan_routes.fetch_public_product",
                        lambda u: parse_public_html(HTML, u))
    with TestClient(create_app(str(tmp_path / "meituan.db"), seed_demo=False)) as client:
        assert client.post("/api/admin/meituan/inspect", json={"url": URL}).status_code == 401
        headers = {"X-Admin-Token": "fixture-admin-token"}
        inspected = client.post("/api/admin/meituan/inspect", json={"url": URL},
                                headers=headers)
        assert inspected.status_code == 200
        assert inspected.json()["display_price_cents"] == 5990
        created = client.post("/api/admin/meituan/collect", json={"url": URL},
                              headers=headers)
        assert created.status_code == 201
        assert created.json()["id"] == 1
        rows = client.get("/api/admin/meituan/observations", headers=headers)
        assert rows.status_code == 200
        assert rows.json()["items"][0]["final_price_verified"] == 0
        assert rows.json()["items"][0]["source_type"] == "public_page"
        assert client.get("/api/stores").json()["stores"] == []
        assert client.get("/api/integrations/status").json()["official_platform_apis"]["meituan"] == "not_connected"


def test_no_price_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("TBB_ADMIN_TOKEN", "test")
    monkeypatch.setattr("app.meituan_routes.fetch_public_product",
                        lambda u: parse_public_html(
                            '<script type="application/ld+json">{"@type":"Product","name":"测试套餐"}</script>', u))
    with TestClient(create_app(str(tmp_path / "partial.db"), seed_demo=False)) as client:
        h = {"X-Admin-Token": "test"}
        assert client.post("/api/admin/meituan/collect",
                           json={"url": URL}, headers=h).status_code == 422
        assert client.get("/api/admin/meituan/observations",
                          headers=h).json()["count"] == 0


def test_alliance_schema_endpoint_is_not_fake_live_call(tmp_path, monkeypatch):
    monkeypatch.setenv("TBB_ADMIN_TOKEN", "test")
    with TestClient(create_app(str(tmp_path / "official.db"), seed_demo=False)) as client:
        r = client.get("/api/admin/meituan/alliance/query",
                       params={"keyword": "麦当劳"}, headers={"X-Admin-Token": "test"})
        assert r.status_code == 200
        assert r.json()["auth_status"] == "credentials_and_signature_required"
        assert "platform" not in r.json()["request_body"]

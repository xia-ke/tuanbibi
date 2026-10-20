"""Meituan Alliance product-query contract and response normalization.

Official schema: https://page.meituan.net/html/1701831807616_1db0df/index.html
The authenticated call itself is NOT implemented: signing is not documented by
the product-schema page, and this module never fabricates credentials or prices.
"""
from decimal import Decimal, InvalidOperation


class AllianceDataError(ValueError):
    pass


def query_body(*, keyword: str | None = None, product_signs: list[str] | None = None,
               page: int = 1, page_size: int = 20, city_id: str | None = None) -> dict:
    if bool(keyword) == bool(product_signs):
        raise AllianceDataError("必须且只能指定关键词或商品标识列表")
    if not 1 <= page <= 100 or not 1 <= page_size <= 20:
        raise AllianceDataError("分页参数无效")
    body = {"pageNo": page, "pageSize": page_size}
    if keyword:
        if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 100:
            raise AllianceDataError("搜索关键词必须为 1-100 字")
        # Official schema prohibits sending platform/bizLine alongside searchText.
        body["searchText"] = keyword.strip()
    else:
        if len(product_signs) > 20 or not all(
            isinstance(s, str) and 1 <= len(s) <= 96 and s.replace("_", "").isalnum()
            for s in product_signs
        ):
            raise AllianceDataError("商品标识列表不合法")
        body.update({"platform": 2, "bizLine": 1,
                     "productViewSignList": list(product_signs)})
    if city_id is not None:
        if not city_id.isdecimal() or len(city_id) > 12:
            raise AllianceDataError("城市编码不合法")
        body["cityId"] = city_id
    return body


def _price_fen(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        cents = Decimal(str(value)) * 100
        if not cents.is_finite() or cents < 0 or cents > 10_000_000 or cents != cents.to_integral_value():
            return None
        return int(cents)
    except (ValueError, InvalidOperation):
        return None


def normalize_response(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise AllianceDataError("美团联盟接口未返回成功响应")
    data = payload.get("data")
    if not isinstance(data, list):
        raise AllianceDataError("商品列表结构不符合官方响应格式")
    products = []
    skipped = 0
    for entry in data:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        detail = entry.get("couponPackDetail")
        if not isinstance(detail, dict) or detail.get("saleStatus") is False:
            skipped += 1
            continue
        title = detail.get("name")
        sell = _price_fen(detail.get("sellPrice"))
        if not isinstance(title, str) or not title.strip() or sell is None:
            skipped += 1
            continue
        platform = detail.get("platform", entry.get("platform"))
        biz_line = detail.get("bizLine", entry.get("bizLine"))
        if (platform is not None and platform != 2) or (biz_line is not None and biz_line != 1):
            skipped += 1
            continue
        brand = entry.get("brandInfo")
        poi = entry.get("availablePoiInfo")
        products.append({
            "platform": "meituan",
            "source_type": "meituan_alliance_response",
            "product_name": title.strip()[:160],
            "product_view_sign": detail.get("productViewSign"),
            "sku_view_id": detail.get("skuViewId"),
            "brand_name": brand.get("brandName") if isinstance(brand, dict) else None,
            "display_price_cents": sell,
            "original_price_cents": _price_fen(detail.get("originalPrice")),
            "sale_status": detail.get("saleStatus"),
            "available_poi_count": poi.get("availablePoiNum") if isinstance(poi, dict) else None,
            "business_scope": "confirmed_dining" if platform == 2 and biz_line == 1 else "unconfirmed",
            "final_price_verified": False,
            "merchant_poi_unresolved": True,
            "note": "API列出的是商品展示价；还需门店归属及用户结算价核验",
        })
    return {"products": products, "skipped": skipped,
            "has_next": payload.get("hasNext") is True,
            "search_id": payload.get("searchId") if isinstance(payload.get("searchId"), str) else None}

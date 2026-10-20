"""Meituan-only discovery endpoints; all outbound probing requires admin access."""
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app import store
from app.meituan_public import fetch_public_product, PublicPageError
from app.meituan_alliance import query_body, normalize_response, AllianceDataError


class MeituanLink(BaseModel):
    url: str = Field(min_length=10, max_length=2048)


def attach_meituan_routes(app: FastAPI, db_path: str, admin_dependency) -> None:
    def inspect(link: MeituanLink) -> dict:
        try:
            return fetch_public_product(link.url)
        except PublicPageError as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"reason": exc.reason, "message": str(exc)},
            ) from exc

    @app.post("/api/admin/meituan/inspect", dependencies=[Depends(admin_dependency)])
    def inspect_product(link: MeituanLink):
        """Fetch and extract public JSON-LD or product meta; no database writes."""
        return inspect(link)

    @app.post("/api/admin/meituan/collect", status_code=201,
              dependencies=[Depends(admin_dependency)])
    def collect_product(link: MeituanLink):
        """Record only explicit public display prices, not inferred checkout totals."""
        result = inspect(link)
        if result["display_price_cents"] is None:
            raise HTTPException(422, {
                "reason": "price_not_disclosed",
                "message": "页面没有明确的商品价格字段，未保存",
            })
        with store.connect(db_path) as db:
            observation_id = store.save_meituan_public_observation(db, result)
        return {
            "id": observation_id,
            **result,
            "notice": "公开页面标价，不代表平台实际支付金额；不自动参与最低价排名",
        }

    @app.get("/api/admin/meituan/observations",
             dependencies=[Depends(admin_dependency)])
    def observations(limit: int = Query(default=20, ge=1, le=100)):
        with store.connect(db_path) as db:
            rows = store.list_meituan_public_observations(db, limit)
        return {"items": rows, "count": len(rows), "source_type": "public_page"}

    @app.get("/api/admin/meituan/alliance/query",
             dependencies=[Depends(admin_dependency)])
    def alliance_query(keyword: str = Query(min_length=1, max_length=100),
                       page: int = Query(default=1, ge=1, le=100)):
        """Return the official Alliance POST JSON. Does not send an unsigned call."""
        try:
            body = query_body(keyword=keyword, page=page)
        except AllianceDataError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "endpoint": "https://media.meituan.com/cps_open/common/api/v1/query_coupon",
            "method": "POST",
            "request_body": body,
            "auth_status": "credentials_and_signature_required",
            "note": "查询接口支持跨品类搜索；返回结果须再次判断是否为到店餐饮",
        }

    @app.post("/api/admin/meituan/alliance/normalize",
              dependencies=[Depends(admin_dependency)])
    def alliance_normalize(payload: dict):
        """Normalize user-supplied official-schema responses; does not validate origin."""
        try:
            result = normalize_response(payload)
        except AllianceDataError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            **result,
            "verification": "user_supplied_payload_not_authenticated",
            "note": "未发起官方接口调用，也没有验证该 JSON 的实际来源",
        }

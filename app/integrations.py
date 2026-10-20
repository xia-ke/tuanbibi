"""Fail-closed platform adapter contract and signed partner price-feed ingestion.

A signed feed authenticates its submitter, NOT its claimed platform price. No official
Meituan/Douyin/Kuaishou API is invoked by this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from typing import Literal, Protocol
from urllib.parse import urlparse
import unicodedata

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, StrictInt, ValidationError, field_validator, model_validator

from app import store

PLATFORM_DOMAINS = {
    "meituan": ("meituan.com",),
    "dianping": ("dianping.com",),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "kuaishou": ("kuaishou.com", "gifshow.com"),
}

MAX_AGE = timedelta(minutes=10)
MAX_VALIDITY = timedelta(hours=24)
MAX_BODY_SIZE = 32768


def platform_url_is_valid(platform: str, url: str) -> bool:
    if not url or platform not in PLATFORM_DOMAINS:
        return False
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return (
            p.scheme == "https" and bool(host)
            and not p.username and not p.password and p.port in (None, 443)
            and any(host == domain or host.endswith("." + domain)
                    for domain in PLATFORM_DOMAINS[platform])
        )
    except ValueError:
        return False


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class QuoteFeed(BaseModel):
    model_config = {"extra": "forbid"}

    event_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")
    product_id: StrictInt = Field(gt=0)
    store_id: StrictInt = Field(gt=0)
    platform: Literal["meituan", "dianping", "douyin", "kuaishou"]
    product_name: str = Field(min_length=2, max_length=120)
    details: str = Field(min_length=3, max_length=400)
    usage_terms: str = Field(min_length=2, max_length=400)
    store_address: str = Field(min_length=2, max_length=200)
    list_price_cents: StrictInt = Field(ge=0, le=10_000_000)
    discount_cents: StrictInt = Field(ge=0, le=10_000_000)
    fee_cents: StrictInt = Field(ge=0, le=10_000_000)
    availability: Literal["available", "sold_out", "offline", "unknown"]
    eligibility: Literal["public", "coupon_required", "member_only", "unknown"]
    quoted_at: datetime
    valid_until: datetime
    source_url: str = Field(min_length=10, max_length=2048)
    note: str = Field(min_length=2, max_length=300)

    @field_validator("source_url")
    @classmethod
    def valid_syntax(cls, value: str):
        if not value.startswith("https://"):
            raise ValueError("需要 HTTPS 来源")
        return value

    @model_validator(mode="after")
    def check_invariants(self):
        if self.discount_cents > self.list_price_cents:
            raise ValueError("优惠不得大于标价")
        if not platform_url_is_valid(self.platform, self.source_url):
            raise ValueError("来源链接非指定平台官方域名")
        if self.quoted_at.utcoffset() is None or self.valid_until.utcoffset() is None:
            raise ValueError("时间必须带时区")
        now = datetime.now(timezone.utc)
        quoted = self.quoted_at.astimezone(timezone.utc)
        expiry = self.valid_until.astimezone(timezone.utc)
        if quoted > now + timedelta(minutes=1) or now - quoted > MAX_AGE:
            raise ValueError("报价记录时间过旧或位于未来")
        if not quoted < expiry <= quoted + MAX_VALIDITY or expiry <= now:
            raise ValueError("报价有效期必须在未来且最多24小时")
        return self


class PlatformAdapter(Protocol):
    """Future authorized API connectors must map native response to QuoteFeed.

    This protocol is a seam, not an implemented platform scraper.
    """

    platform: str

    def fetch_for_authorized_merchant(self, merchant_id: str) -> list[QuoteFeed]: ...


def _keys() -> dict[str, str]:
    try:
        loaded = json.loads(os.environ.get("TBB_PARTNER_KEYS_JSON", "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {key: val for key, val in loaded.items()
            if isinstance(key, str) and key.isascii() and key.isidentifier()
            and isinstance(val, str) and len(val.encode()) >= 32}


def _verify_request(partner_id: str | None, timestamp: str | None,
                    signature: str | None, body: bytes) -> str:
    keys = _keys()
    if not keys:
        raise HTTPException(503, "未配置合作方上报通道")
    if partner_id not in keys or not timestamp or not signature:
        raise HTTPException(401, "签名认证失败")
    if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 12:
        raise HTTPException(401, "签名认证失败")
    sent_at = int(timestamp)
    if abs(datetime.now(timezone.utc).timestamp() - sent_at) > 300:
        raise HTTPException(401, "签名已过期")
    if (not signature.startswith("sha256=") or len(signature) != 71 or
        any(c not in "0123456789abcdef" for c in signature[7:])):
        raise HTTPException(401, "签名认证失败")
    base = timestamp.encode("ascii") + b"." + body
    expect = "sha256=" + hmac.new(keys[partner_id].encode("utf-8"), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, signature):
        raise HTTPException(401, "签名认证失败")
    return partner_id


def attach_feed_routes(app: FastAPI, db_path: str) -> None:
    router = APIRouter()

    @router.get("/api/integrations/status")
    def integration_status():
        # Do not suggest official APIs are configured, even when a partner feed key exists.
        return {
            "official_platform_apis": {name: "not_connected" for name in PLATFORM_DOMAINS},
            "signed_partner_feed": "configured" if _keys() else "disabled",
            "note": "签名只能证明合作方提交来源，不能证明价格是平台官方实时价。",
        }

    @router.post("/api/partner/quotes", status_code=201)
    async def receive_partner_quote(
        request: Request,
        x_partner_id: str | None = Header(default=None),
        x_timestamp: str | None = Header(default=None),
        x_signature: str | None = Header(default=None),
    ):
        # Reject oversize bodies before attempting to parse or record them.
        if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > MAX_BODY_SIZE:
            raise HTTPException(413, "请求体过大")
        body = await request.body()
        if len(body) > MAX_BODY_SIZE:
            raise HTTPException(413, "请求体过大")
        partner_id = _verify_request(x_partner_id, x_timestamp, x_signature, body)
        try:
            quote = QuoteFeed.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "报价数据验证未通过")
        with store.connect(db_path) as db:
            row = db.execute(
                """SELECT p.id AS product_id, p.name, p.details, p.usage_terms,
                          s.id AS store_id, s.address
                   FROM products p JOIN stores s ON s.id=p.store_id WHERE p.id=?""",
                (quote.product_id,),
            ).fetchone()
            if (row is None or row["store_id"] != quote.store_id or
                any(normalized(a) != normalized(b) for a, b in [
                    (row["name"], quote.product_name),
                    (row["details"], quote.details),
                    (row["usage_terms"], quote.usage_terms),
                    (row["address"], quote.store_address),
                ])):
                raise HTTPException(422, "门店、套餐或使用条件无法严格匹配；请人工复核")
            try:
                cursor = db.execute(
                    """INSERT INTO partner_quotes(
                       product_id, partner_id, event_id, platform,
                       list_price_cents,discount_cents,fee_cents,
                       availability,eligibility,source_url,note,
                       quoted_at,valid_until,received_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        quote.product_id, partner_id, quote.event_id, quote.platform,
                        quote.list_price_cents, quote.discount_cents, quote.fee_cents,
                        quote.availability, quote.eligibility, quote.source_url,
                        quote.note, quote.quoted_at.astimezone(timezone.utc).isoformat(),
                        quote.valid_until.astimezone(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except store.sqlite3.IntegrityError:
                raise HTTPException(409, "事件已录入，不允许重放")
        return {"id": cursor.lastrowid, "source_type": "partner_feed",
                "attestation": "signed_partner_submission_not_official_verification"}

    app.include_router(router)

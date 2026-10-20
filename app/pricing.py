"""Conservative evidence-aware comparison. Money is always integer fen."""
from datetime import datetime, timedelta, timezone

MAX_FEED_AGE = timedelta(minutes=15)


def payable_cents(list_price_cents: int, discount_cents: int, fee_cents: int) -> int:
    if any(isinstance(v, bool) or not isinstance(v, int)
           for v in (list_price_cents, discount_cents, fee_cents)):
        raise ValueError("价格必须以整数分表示")
    if min(list_price_cents, discount_cents, fee_cents) < 0 or discount_cents > list_price_cents:
        raise ValueError("金额不合法：折扣不能超过标价，金额不能为负")
    return list_price_cents - discount_cents + fee_cents


def _parse(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc) if dt.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def quote_status(quote: dict, now: datetime) -> str | None:
    source = quote.get("source_type")
    if source == "demo":
        return "演示报价，不参与比价"
    if source == "manual":
        return "人工录入价格尚未经过独立核验"
    if source != "partner_feed":
        return "数据来源未建立可信接入"
    if quote.get("availability") != "available":
        return "商品未确认可售"
    if quote.get("eligibility") != "public":
        return "购买资格或领券条件需单独确认"
    quoted = _parse(quote.get("quoted_at"))
    expiry = _parse(quote.get("valid_until"))
    if not quoted or not expiry or quoted > now + timedelta(minutes=1):
        return "时间信息缺失或无效"
    if quoted < now - MAX_FEED_AGE:
        return "合作方报价超过15分钟未更新"
    if expiry <= now:
        return "合作方报价已过期"
    if not quote.get("source_url"):
        return "缺少原始商品链接"
    return None


def comparison_for(product: dict, quotes: list[dict], now: datetime | None = None) -> dict:
    """Compare ONLY recent signed partner submissions with unrestricted availability.

    HMAC establishes submitter identity, not platform correctness. We label the
    output a partner-reported spread; never call it an official real-time saving.
    """
    now = now or datetime.now(timezone.utc)
    normalized = []
    for source_quote in quotes:
        q = dict(source_quote)
        q["payable_cents"] = payable_cents(
            q["list_price_cents"], q["discount_cents"], q["fee_cents"])
        reason = quote_status(q, now)
        q["comparison_eligible"] = reason is None
        q["comparison_reason"] = reason
        normalized.append(q)
    normalized.sort(key=lambda q: (q["payable_cents"], q["platform"]))
    eligible = [q for q in normalized if q["comparison_eligible"]]
    # Each platform has at most one latest quote after store.latest_quotes().
    distinct_platforms = {q["platform"] for q in eligible}
    comparable = len(distinct_platforms) >= 2
    lowest = min((q["payable_cents"] for q in eligible), default=None)
    spread = (max(q["payable_cents"] for q in eligible) - lowest) if comparable else None
    return {
        "product": product,
        "quotes": normalized,
        "real_comparison_ready": comparable,
        "lowest_real_cents": lowest if comparable else None,
        "possible_saving_cents": spread,
        "comparison_basis": "partner_reported" if comparable else "unavailable",
        "contains_demo": any(q["source_type"] == "demo" for q in normalized),
        "notice": "有效报价来自签名合作方，不代表官方实时价；到手价请在原平台复核。",
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

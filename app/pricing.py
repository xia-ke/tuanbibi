"""Money calculation and comparison rules. All money values are integer fen."""

from collections import defaultdict
from datetime import datetime, timezone


def payable_cents(list_price_cents: int, discount_cents: int, fee_cents: int) -> int:
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (list_price_cents, discount_cents, fee_cents)):
        raise ValueError("价格必须以整数分表示")
    if min(list_price_cents, discount_cents, fee_cents) < 0 or discount_cents > list_price_cents:
        raise ValueError("金额不合法：折扣不能超过标价，金额不能为负")
    return list_price_cents - discount_cents + fee_cents


def comparison_for(product: dict, quotes: list[dict]) -> dict:
    """Only compare quotes sharing one explicit product id / identical terms.

    A demo quote may be displayed but must NEVER participate in a real saving claim.
    Manually supplied data is marked as manual and is not called verified/live.
    """
    normalized = []
    for q in quotes:
        q = dict(q)
        q["payable_cents"] = payable_cents(
            q["list_price_cents"], q["discount_cents"], q["fee_cents"]
        )
        normalized.append(q)
    normalized.sort(key=lambda q: (q["payable_cents"], q["platform"]))

    real = [q for q in normalized if q["source_type"] != "demo"]
    distinct_platforms = {q["platform"] for q in real}
    comparable = len(distinct_platforms) >= 2
    best = min((q["payable_cents"] for q in real), default=None)
    saving = (max(q["payable_cents"] for q in real) - best) if comparable else None
    return {
        "product": product,
        "quotes": normalized,
        "real_comparison_ready": comparable,
        "lowest_real_cents": best if comparable else None,
        "possible_saving_cents": saving,
        "contains_demo": any(q["source_type"] == "demo" for q in normalized),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

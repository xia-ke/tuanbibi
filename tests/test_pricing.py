import pytest
from app.pricing import payable_cents, comparison_for


def test_effective_price_is_integer_fen():
    assert payable_cents(1990, 300, 50) == 1740
    assert payable_cents(0, 0, 0) == 0


@pytest.mark.parametrize("values", [(-1, 0, 0), (100, -1, 0), (100, 101, 0), (100, 0, -1)])
def test_reject_bad_amount(values):
    with pytest.raises(ValueError):
        payable_cents(*values)


def test_demo_cannot_claim_real_savings():
    result = comparison_for({"id": 1}, [
        {"platform": "meituan", "source_type": "demo", "list_price_cents": 100, "discount_cents": 20, "fee_cents": 0},
        {"platform": "douyin", "source_type": "demo", "list_price_cents": 90, "discount_cents": 0, "fee_cents": 0},
    ])
    assert not result["real_comparison_ready"]
    assert result["possible_saving_cents"] is None


def test_manual_is_not_verified_as_live_comparison():
    result = comparison_for({"id": 1}, [
        {"platform": "meituan", "source_type": "manual", "list_price_cents": 1000, "discount_cents": 100, "fee_cents": 0},
        {"platform": "douyin", "source_type": "manual", "list_price_cents": 850, "discount_cents": 50, "fee_cents": 0},
    ])
    assert not result["real_comparison_ready"]
    assert result["lowest_real_cents"] is None
    assert result["possible_saving_cents"] is None
    assert result["quotes"][0]["platform"] == "douyin"



def test_partner_feed_rejects_stale_and_restricted_prices():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    base = {
        "source_type": "partner_feed", "platform": "douyin",
        "list_price_cents": 1000, "discount_cents": 50, "fee_cents": 0,
        "availability": "available", "eligibility": "public",
        "source_url": "https://www.douyin.com/item/123",
        "quoted_at": now.isoformat(),
        "valid_until": (now + timedelta(minutes=30)).isoformat(),
    }
    assert comparison_for({"id": 1}, [base], now=now)["quotes"][0]["comparison_eligible"]
    for changes in (
        {"quoted_at": (now - timedelta(minutes=16)).isoformat()},
        {"valid_until": (now - timedelta(seconds=1)).isoformat()},
        {"availability": "sold_out"},
        {"eligibility": "member_only"},
        {"source_url": ""},
    ):
        quote = dict(base, **changes)
        result = comparison_for({"id": 1}, [quote], now=now)
        assert not result["quotes"][0]["comparison_eligible"]
        assert result["possible_saving_cents"] is None


def test_partner_feed_multiplatform_spread():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    base = {
        "source_type": "partner_feed", "list_price_cents": 2300,
        "discount_cents": 100, "fee_cents": 0,
        "availability": "available", "eligibility": "public",
        "source_url": "https://www.douyin.com/item/1",
        "quoted_at": now.isoformat(),
        "valid_until": (now + timedelta(minutes=25)).isoformat(),
    }
    result = comparison_for({"id": 1}, [
        dict(base, platform="douyin", discount_cents=100),
        dict(base, platform="meituan", discount_cents=400),
    ], now=now)
    assert result["real_comparison_ready"]
    assert result["comparison_basis"] == "partner_reported"
    assert result["lowest_real_cents"] == 1900
    assert result["possible_saving_cents"] == 300

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


def test_non_demo_comparison_and_sorting():
    result = comparison_for({"id": 1}, [
        {"platform": "meituan", "source_type": "manual", "list_price_cents": 1000, "discount_cents": 100, "fee_cents": 0},
        {"platform": "douyin", "source_type": "manual", "list_price_cents": 850, "discount_cents": 50, "fee_cents": 0},
    ])
    assert result["real_comparison_ready"]
    assert result["lowest_real_cents"] == 800
    assert result["possible_saving_cents"] == 100
    assert result["quotes"][0]["platform"] == "douyin"

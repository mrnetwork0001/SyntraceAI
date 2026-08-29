"""Unit tests for the billing math: tier discounts, tax, coupons, totals."""

from app.pricing import apply_coupon, apply_tier_discount, compute_tax, final_total


class TestTierDiscounts:
    """apply_tier_discount picks the right rate for each plan."""

    def test_free_tier_pays_full_price(self) -> None:
        assert apply_tier_discount(80.0, "free") == 80.0

    def test_plus_tier_small_order(self) -> None:
        assert apply_tier_discount(80.0, "plus") == 76.0

    def test_pro_tier_small_order(self) -> None:
        assert apply_tier_discount(80.0, "pro") == 72.0

    def test_mid_size_order_gets_volume_bonus(self) -> None:
        # pro 10% + 3% volume bonus on a 200.00 order
        assert apply_tier_discount(200.0, "pro") == 174.0

    def test_enterprise_large_order_gets_best_rate(self) -> None:
        assert apply_tier_discount(1000.0, "enterprise") == 750.0

    def test_zero_subtotal_costs_nothing(self) -> None:
        assert apply_tier_discount(0.0, "pro") == 0.0


class TestRegionalTax:
    """compute_tax applies the configured regional rates."""

    def test_eu_vat(self) -> None:
        assert compute_tax(200.0, "eu") == 42.0

    def test_uk_vat(self) -> None:
        assert compute_tax(200.0, "uk") == 40.0

    def test_us_sales_tax(self) -> None:
        assert compute_tax(200.0, "us") == 14.0

    def test_us_small_order_is_exempt(self) -> None:
        assert compute_tax(20.0, "us") == 0.0

    def test_unknown_region_uses_default_rate(self) -> None:
        assert compute_tax(200.0, "atlantis") == 10.0

    def test_no_tax_on_zero_amount(self) -> None:
        assert compute_tax(0.0, "eu") == 0.0


class TestCoupons:
    """apply_coupon takes a percentage off the amount."""

    def test_typical_coupon(self) -> None:
        assert apply_coupon(100.0, 10.0) == 90.0

    def test_no_coupon_leaves_amount_unchanged(self) -> None:
        assert apply_coupon(55.0, 0.0) == 55.0

    def test_coupon_on_zero_amount(self) -> None:
        assert apply_coupon(0.0, 25.0) == 0.0


class TestFinalTotal:
    """final_total chains discount, coupon, and tax."""

    def test_typical_pro_invoice_in_eu(self) -> None:
        assert final_total(200.0, "pro", "eu") == 210.54

    def test_invoice_with_coupon_in_us(self) -> None:
        assert final_total(200.0, "free", "us", coupon_pct=10.0) == 186.82

    def test_zero_subtotal_invoice(self) -> None:
        assert final_total(0.0, "plus", "eu") == 0.0

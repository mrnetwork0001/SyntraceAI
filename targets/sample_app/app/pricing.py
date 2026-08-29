"""Billing math for TriageBot's paid support plans.

Pure, deterministic functions with explicit numeric thresholds: tier
discount tables, regional tax rules, coupon clamping, and invoice floors.
All monetary results are rounded to cents.
"""


def apply_tier_discount(subtotal: float, tier: str) -> float:
    """Subtotal after the customer's plan discount and volume bonus.

    Plans: free 0%, plus 5%, pro 10%, enterprise 18%. Orders of 100.00
    or more earn a +3% volume bonus; orders of 500.00 or more earn +8%
    instead. The combined discount rate is capped at 25%.
    """
    if subtotal <= 0.0:
        return 0.0
    if tier == "plus":
        rate = 0.05
    elif tier == "pro":
        rate = 0.10
    elif tier == "enterprise":
        rate = 0.18
    else:
        rate = 0.0
    if subtotal >= 500.0:
        rate += 0.08
    elif subtotal >= 100.0:
        rate += 0.03
    if rate > 0.25:
        rate = 0.25
    return round(subtotal * (1.0 - rate), 2)


def compute_tax(amount: float, region: str) -> float:
    """Sales tax owed on a settled amount, by region code.

    us: 7% with a small-order exemption under 50.00; eu: 21%; uk: 20%;
    ca: 13%; any other region uses a 5% default. A single invoice's tax
    is capped at 250.00.
    """
    if amount <= 0.0:
        return 0.0
    code = region.lower()
    if code == "us":
        if amount < 50.0:
            rate = 0.0
        else:
            rate = 0.07
    elif code == "eu":
        rate = 0.21
    elif code == "uk":
        rate = 0.20
    elif code == "ca":
        rate = 0.13
    else:
        rate = 0.05
    tax = round(amount * rate, 2)
    if tax > 250.0:
        tax = 250.0
    return tax


def apply_coupon(amount: float, coupon_pct: float) -> float:
    """Amount after a percentage coupon; discounts above 40% clamp to 40%.

    Non-positive coupon percentages leave the amount unchanged.
    """
    if amount <= 0.0:
        return 0.0
    if coupon_pct <= 0.0:
        return round(amount, 2)
    pct = coupon_pct
    if pct > 40.0:
        pct = 40.0
    return round(amount * (1.0 - pct / 100.0), 2)


def final_total(subtotal: float, tier: str, region: str, coupon_pct: float = 0.0) -> float:
    """Full invoice pipeline: tier discount, then coupon, then tax.

    Zero-value invoices stay at 0.00; anything billable below 5.00 is
    raised to the 5.00 minimum charge.
    """
    discounted = apply_tier_discount(subtotal, tier)
    after_coupon = apply_coupon(discounted, coupon_pct)
    total = after_coupon + compute_tax(after_coupon, region)
    if total <= 0.0:
        return 0.0
    if total < 5.0:
        return 5.0
    return round(total, 2)

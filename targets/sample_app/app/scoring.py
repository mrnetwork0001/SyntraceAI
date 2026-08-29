"""Triage scoring and routing rules for TriageBot.

Pure, deterministic functions that convert a parsed triage result into
routing decisions: a 0-100 priority score, an escalation verdict with
per-category thresholds, and first-response SLA lanes.
"""


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the closed interval [``low``, ``high``]."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def priority_score(priority: int, confidence: float) -> float:
    """Blend priority and model confidence into a 0-100 triage score.

    Priority dominates at 18 points per level; confidence adds up to 10
    points. High-priority calls backed by strong confidence (>= 4 and
    >= 0.8) earn an 8-point boost; scores backed by confidence under 0.3
    are penalized by 5 points. The result is clamped to [0, 100].
    """
    level = clamp(float(priority), 1.0, 5.0)
    trust = clamp(confidence, 0.0, 1.0)
    score = level * 18.0 + trust * 10.0
    if level >= 4.0 and trust >= 0.8:
        score += 8.0
    if trust < 0.3:
        score -= 5.0
    return clamp(round(score, 2), 0.0, 100.0)


def escalation_required(score: float, category: str) -> bool:
    """Whether the ticket must be escalated to the on-call engineer.

    Bugs escalate at 60, billing at 65, performance at 70; everything
    else uses the default bar of 75.
    """
    if category == "bug":
        threshold = 60.0
    elif category == "billing":
        threshold = 65.0
    elif category == "performance":
        threshold = 70.0
    else:
        threshold = 75.0
    return score >= threshold


def sla_hours(priority: int) -> int:
    """First-response SLA in hours for a priority level (1 low .. 5 critical)."""
    if priority >= 5:
        return 1
    if priority == 4:
        return 4
    if priority == 3:
        return 24
    if priority == 2:
        return 48
    return 72

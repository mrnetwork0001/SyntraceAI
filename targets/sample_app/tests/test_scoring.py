"""Unit tests for triage scoring, escalation, and SLA rules."""

from app.scoring import clamp, escalation_required, priority_score, sla_hours


class TestPriorityScore:
    """priority_score blends priority level and model confidence."""

    def test_critical_with_high_confidence_maxes_out(self) -> None:
        assert priority_score(5, 0.9) == 100.0

    def test_mid_priority_ticket(self) -> None:
        assert priority_score(3, 0.6) == 60.0

    def test_low_confidence_is_penalized(self) -> None:
        assert priority_score(2, 0.1) == 32.0

    def test_out_of_range_priority_is_clamped(self) -> None:
        assert priority_score(9, 0.5) == priority_score(5, 0.5)


class TestEscalation:
    """escalation_required compares the score against per-category bars."""

    def test_hot_bug_escalates(self) -> None:
        assert escalation_required(90.0, "bug") is True

    def test_quiet_general_ticket_does_not(self) -> None:
        assert escalation_required(30.0, "general") is False

    def test_billing_uses_a_lower_bar(self) -> None:
        assert escalation_required(70.0, "billing") is True

    def test_performance_incident_escalates(self) -> None:
        assert escalation_required(90.0, "performance") is True


class TestSlaHours:
    """sla_hours maps priority levels to response lanes."""

    def test_critical_gets_one_hour(self) -> None:
        assert sla_hours(5) == 1

    def test_high_priority_lane(self) -> None:
        assert sla_hours(4) == 4

    def test_mid_priority_lane(self) -> None:
        assert sla_hours(3) == 24

    def test_lowest_priority_lane(self) -> None:
        assert sla_hours(1) == 72


class TestClamp:
    """clamp keeps values inside the given interval."""

    def test_value_inside_range_is_untouched(self) -> None:
        assert clamp(0.5, 0.0, 1.0) == 0.5

    def test_value_below_range(self) -> None:
        assert clamp(-2.0, 0.0, 1.0) == 0.0

    def test_value_above_range(self) -> None:
        assert clamp(9.9, 0.0, 1.0) == 1.0

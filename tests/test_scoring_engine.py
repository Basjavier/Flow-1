"""
Unit tests for scoring/engine.py — pure functions, no DB or network.
"""
import pytest

from scoring.engine import (
    ScoreBreakdown,
    classify_alert_level,
    compute_score,
    score_delta_fiscal,
    score_price_m2,
    score_price_reduction,
    score_time_on_market,
)


# ---------------------------------------------------------------------------
# score_price_m2
# ---------------------------------------------------------------------------


class TestScorePriceM2:
    def test_at_median_returns_100(self):
        # formula: max(0, 100 - ((p/m - 1) * 100))  → 100 - 0 = 100
        assert score_price_m2(1_000_000, 1_000_000) == 100.0

    def test_below_median_capped_at_100(self):
        # price < median → ratio < 1 → raw > 100 → capped at 100
        result = score_price_m2(500_000, 1_000_000)
        assert result == 100.0

    def test_50pct_above_median_gives_50(self):
        # ratio = 1.5 → 100 - (0.5 * 100) = 50
        assert score_price_m2(1_500_000, 1_000_000) == pytest.approx(50.0)

    def test_double_median_gives_0(self):
        # ratio = 2.0 → 100 - (1.0 * 100) = 0
        assert score_price_m2(2_000_000, 1_000_000) == pytest.approx(0.0)

    def test_triple_median_floored_at_0(self):
        # ratio = 3.0 → 100 - (2.0 * 100) = -100 → max(0, -100) = 0
        assert score_price_m2(3_000_000, 1_000_000) == 0.0

    def test_zero_median_returns_neutral(self):
        assert score_price_m2(1_000_000, 0) == 50.0

    def test_output_range(self):
        for ratio in [0.1, 0.5, 1.0, 1.5, 2.0, 3.0]:
            result = score_price_m2(ratio * 1_000_000, 1_000_000)
            assert 0.0 <= result <= 100.0


# ---------------------------------------------------------------------------
# score_delta_fiscal
# ---------------------------------------------------------------------------


class TestScoreDeltaFiscal:
    def test_high_delta_returns_100(self):
        # delta = 0.90 → score 100
        assert score_delta_fiscal(9_000_000, 10_000_000) == 100.0

    def test_exact_085_threshold(self):
        assert score_delta_fiscal(8_500_000, 10_000_000) == 100.0

    def test_mid_delta_07(self):
        # delta=0.70 → falls in [0.65, 0.75) band → 70.0
        result = score_delta_fiscal(7_000_000, 10_000_000)
        assert result == pytest.approx(70.0)

    def test_low_delta_05(self):
        result = score_delta_fiscal(5_000_000, 10_000_000)
        assert result == pytest.approx(40.0)

    def test_very_low_delta_03(self):
        result = score_delta_fiscal(3_000_000, 10_000_000)
        assert result == pytest.approx(20.0)

    def test_zero_precio_returns_neutral(self):
        assert score_delta_fiscal(5_000_000, 0) == 50.0

    def test_output_always_in_range(self):
        for avaluo, precio in [(1_000_000, 10_000_000), (9_000_000, 10_000_000)]:
            r = score_delta_fiscal(avaluo, precio)
            assert 0.0 <= r <= 100.0


# ---------------------------------------------------------------------------
# score_time_on_market
# ---------------------------------------------------------------------------


class TestScoreTimeOnMarket:
    def test_fresh_listing_low_score(self):
        # < 7 days → neutral (30)
        assert score_time_on_market(3) == pytest.approx(30.0)

    def test_one_week_low(self):
        # condition is `days > 7` (strict) — 7 is not > 7, so stays at 30
        assert score_time_on_market(7) == pytest.approx(30.0)
        # 8 days crosses the threshold into the 35-point band
        assert score_time_on_market(8) == pytest.approx(35.0)

    def test_one_month_mid(self):
        assert score_time_on_market(35) == pytest.approx(55.0)

    def test_two_months_high(self):
        assert score_time_on_market(65) == pytest.approx(75.0)

    def test_three_months_very_high(self):
        assert score_time_on_market(95) == pytest.approx(85.0)

    def test_four_months_max(self):
        assert score_time_on_market(150) == pytest.approx(95.0)

    def test_output_always_in_range(self):
        for days in [0, 7, 14, 30, 60, 90, 180, 365]:
            r = score_time_on_market(days)
            assert 0.0 <= r <= 100.0


# ---------------------------------------------------------------------------
# score_price_reduction
# ---------------------------------------------------------------------------


class TestScorePriceReduction:
    def test_no_reduction_neutral(self):
        assert score_price_reduction(1_000_000, 1_000_000) == 50.0

    def test_price_increased_neutral(self):
        # price went UP — no reduction signal
        assert score_price_reduction(1_100_000, 1_000_000) == 50.0

    def test_zero_initial_neutral(self):
        assert score_price_reduction(900_000, 0) == 50.0

    def test_small_reduction_3pct(self):
        # 3% < 5% threshold → 55
        assert score_price_reduction(970_000, 1_000_000) == pytest.approx(55.0)

    def test_reduction_7pct(self):
        # > 5% → 65
        assert score_price_reduction(930_000, 1_000_000) == pytest.approx(65.0)

    def test_reduction_12pct(self):
        # > 10% → 80
        assert score_price_reduction(880_000, 1_000_000) == pytest.approx(80.0)

    def test_reduction_16pct(self):
        # > 15% → 90
        assert score_price_reduction(840_000, 1_000_000) == pytest.approx(90.0)

    def test_reduction_25pct(self):
        # > 20% → 100
        assert score_price_reduction(750_000, 1_000_000) == pytest.approx(100.0)

    def test_output_always_in_range(self):
        for actual, initial in [(1_000_000, 1_000_000), (800_000, 1_000_000), (500_000, 1_000_000)]:
            r = score_price_reduction(actual, initial)
            assert 0.0 <= r <= 100.0


# ---------------------------------------------------------------------------
# compute_score (composite)
# ---------------------------------------------------------------------------


class TestComputeScore:
    def test_returns_score_breakdown(self):
        result = compute_score(
            precio_m2=1_000_000,
            median_m2=1_200_000,
            days_on_market=45,
            precio_actual=50_000_000,
            precio_inicial=55_000_000,
        )
        assert isinstance(result, ScoreBreakdown)

    def test_without_sii_weights_sum(self):
        # Weights without SII: 55% price_m2 + 30% tom + 15% reduction
        s_pm2 = score_price_m2(800_000, 1_000_000)   # below median → high
        s_tom = score_time_on_market(50)
        s_red = score_price_reduction(800_000, 1_000_000)

        expected = s_pm2 * 0.55 + s_tom * 0.30 + s_red * 0.15
        result = compute_score(
            precio_m2=800_000,
            median_m2=1_000_000,
            days_on_market=50,
            precio_actual=800_000,
            precio_inicial=1_000_000,
            avaluo_fiscal=None,
        )
        assert result.total == pytest.approx(expected, abs=0.01)
        assert result.has_sii_data is False
        assert result.delta_fiscal is None

    def test_with_sii_weights_sum(self):
        # Weights with SII: 40% price_m2 + 30% fiscal + 20% tom + 10% reduction
        # Use precio_actual=10M for SII delta, and no price reduction (actual > initial)
        precio_actual = 10_000_000
        avaluo = 8_000_000
        s_pm2 = score_price_m2(800_000, 1_000_000)             # price/m² vs median
        s_fis = score_delta_fiscal(avaluo, precio_actual)       # avaluo/precio
        s_tom = score_time_on_market(50)
        # precio_actual (10M) > precio_inicial (1M) → no reduction → neutral 50
        s_red = score_price_reduction(precio_actual, 1_000_000)

        expected = s_pm2 * 0.40 + s_fis * 0.30 + s_tom * 0.20 + s_red * 0.10
        result = compute_score(
            precio_m2=800_000,
            median_m2=1_000_000,
            days_on_market=50,
            precio_actual=precio_actual,
            precio_inicial=1_000_000,
            avaluo_fiscal=avaluo,
        )
        assert result.total == pytest.approx(expected, abs=0.01)
        assert result.has_sii_data is True
        assert result.delta_fiscal is not None

    def test_total_always_in_range(self):
        for p_m2, median, days in [
            (500_000, 1_000_000, 5),
            (1_500_000, 1_000_000, 180),
            (1_000_000, 1_000_000, 30),
        ]:
            r = compute_score(p_m2, median, days, p_m2, None)
            assert 0.0 <= r.total <= 100.0


# ---------------------------------------------------------------------------
# classify_alert_level
# ---------------------------------------------------------------------------


class TestClassifyAlertLevel:
    def test_high_score_returns_high(self):
        assert classify_alert_level(80.0) == "HIGH"

    def test_exact_high_threshold(self):
        assert classify_alert_level(75.0) == "HIGH"

    def test_medium_range(self):
        assert classify_alert_level(65.0) == "MEDIUM"

    def test_exact_medium_threshold(self):
        assert classify_alert_level(60.0) == "MEDIUM"

    def test_below_medium_returns_none(self):
        assert classify_alert_level(59.9) is None

    def test_zero_returns_none(self):
        assert classify_alert_level(0.0) is None

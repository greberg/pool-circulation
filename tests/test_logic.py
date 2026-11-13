"""Unit tests for the pure decision logic and price parsing.

Run with:  python3 -m unittest discover -s tests
No Home Assistant install needed — the component package is loaded without
executing its __init__.py.
"""
import importlib
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "pool_circulation"
_pkg = types.ModuleType("pc")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules["pc"] = _pkg

logic = importlib.import_module("pc.logic")
prices_mod = importlib.import_module("pc.prices")
const = importlib.import_module("pc.const")

Inputs, Settings, decide = logic.Inputs, logic.Settings, logic.decide
OFF, LOW, MEDIUM, HIGH = const.MODE_OFF, const.MODE_LOW, const.MODE_MEDIUM, const.MODE_HIGH
SUMMER, WINTER = const.SEASON_SUMMER, const.SEASON_WINTER

# Cheap at night, expensive 07–09 and 17–20
PRICES = [0.10, 0.08, 0.05, 0.06, 0.09, 0.20, 0.50, 1.20, 1.50, 1.10, 0.70, 0.60,
          0.55, 0.50, 0.45, 0.60, 0.90, 1.60, 1.80, 1.70, 1.00, 0.40, 0.30, 0.20]


def at(hour, minute=0):
    return datetime(2026, 11, 13, hour, minute)


class SafetyTests(unittest.TestCase):
    def test_pool_critical_beats_everything_but_bursts(self):
        d = decide(Inputs(now=at(18), season=WINTER, pool_temp=3.8, outdoor_temp=6,
                          prices=PRICES, is_peak=True, automation_enabled=False), Settings())
        self.assertEqual(d.mode, MEDIUM)
        self.assertEqual(d.safety, logic.SAFETY_POOL_CRITICAL)
        self.assertTrue(d.enforce)

    def test_pool_critical_in_summer_too(self):
        d = decide(Inputs(now=at(18), season=SUMMER, pool_temp=3.5, outdoor_temp=10), Settings())
        self.assertEqual(d.mode, MEDIUM)

    def test_pool_critical_hysteresis(self):
        s = Settings()
        held = decide(Inputs(now=at(18), season=WINTER, pool_temp=4.3, outdoor_temp=8,
                             prices=PRICES, prev_safety=logic.SAFETY_POOL_CRITICAL), s)
        self.assertEqual(held.safety, logic.SAFETY_POOL_CRITICAL)
        released = decide(Inputs(now=at(18), season=WINTER, pool_temp=4.6, outdoor_temp=8,
                                 prices=PRICES, prev_safety=logic.SAFETY_POOL_CRITICAL), s)
        self.assertIsNone(released.safety)

    def test_frost_runs_low_even_at_peak(self):
        d = decide(Inputs(now=at(18), season=WINTER, pool_temp=6, outdoor_temp=-3,
                          prices=PRICES, is_peak=True), Settings())
        self.assertEqual((d.mode, d.safety), (LOW, logic.SAFETY_FROST))
        self.assertEqual(d.plan, [LOW] * 24)

    def test_frost_ignores_cooldown_and_automation_off(self):
        d = decide(Inputs(now=at(3), season=WINTER, pool_temp=6, outdoor_temp=0,
                          in_cooldown=True, automation_enabled=False), Settings())
        self.assertEqual(d.mode, LOW)

    def test_winter_without_outdoor_sensor_fails_safe(self):
        d = decide(Inputs(now=at(12), season=WINTER, pool_temp=6, outdoor_temp=None,
                          prices=PRICES), Settings())
        self.assertEqual((d.mode, d.safety), (LOW, logic.SAFETY_SENSOR_FAILSAFE))

    def test_extra_filter_overrides_frost(self):
        d = decide(Inputs(now=at(12), season=WINTER, outdoor_temp=-5, extra_filter=True), Settings())
        self.assertEqual(d.mode, HIGH)


class WinterTests(unittest.TestCase):
    def test_cold_tier_skips_priciest_non_adjacent_hours(self):
        d = decide(Inputs(now=at(2), season=WINTER, pool_temp=6, outdoor_temp=3,
                          prices=PRICES), Settings(winter_skip_hours=4))
        self.assertEqual(d.tier, logic.TIER_COLD)
        skipped = [h for h, m in enumerate(d.plan) if m == OFF]
        self.assertEqual(len(skipped), 4)
        for a, b in zip(skipped, skipped[1:]):
            self.assertGreater(b - a, 1, f"adjacent skips {skipped}")
        self.assertIn(18, skipped)  # the priciest hour
        self.assertEqual(d.mode, LOW)

    def test_cold_tier_off_during_skipped_hour(self):
        d = decide(Inputs(now=at(18), season=WINTER, pool_temp=6, outdoor_temp=3,
                          prices=PRICES), Settings())
        self.assertEqual(d.mode, OFF)

    def test_rest_tier_runs_cheapest_hours_only(self):
        s = Settings(winter_min_hours=2)
        on = decide(Inputs(now=at(2), season=WINTER, pool_temp=5, outdoor_temp=8, prices=PRICES), s)
        off = decide(Inputs(now=at(12), season=WINTER, pool_temp=5, outdoor_temp=8, prices=PRICES), s)
        self.assertEqual(on.tier, logic.TIER_REST)
        self.assertEqual((on.mode, off.mode), (LOW, OFF))
        self.assertEqual(sum(1 for m in on.plan if m == LOW), 2)

    def test_algae_tier_when_pool_warm(self):
        d = decide(Inputs(now=at(1), season=WINTER, pool_temp=8, outdoor_temp=9,
                          prices=PRICES), Settings(winter_algae_hours=4))
        self.assertEqual(d.tier, logic.TIER_ALGAE)
        self.assertEqual(sum(1 for m in d.plan if m == LOW), 4)

    def test_winter_must_run_late_in_day(self):
        # 23:00, nothing run yet, 2 h target, no cheap hours left → run now
        d = decide(Inputs(now=at(22, 30), season=WINTER, pool_temp=5, outdoor_temp=8,
                          prices=PRICES, hours_low=0), Settings(winter_min_hours=2))
        self.assertEqual((d.mode, d.rule), (LOW, logic.RULE_MUST_RUN))

    def test_winter_never_touches_medium_on_schedule(self):
        for h in range(24):
            d = decide(Inputs(now=at(h), season=WINTER, pool_temp=8, outdoor_temp=9,
                              prices=PRICES), Settings())
            self.assertIn(d.mode, (OFF, LOW))

    def test_winter_reactive_without_prices(self):
        d = decide(Inputs(now=at(12), season=WINTER, pool_temp=6, outdoor_temp=3,
                          is_peak=True), Settings())
        self.assertEqual((d.mode, d.rule), (OFF, logic.RULE_REACTIVE))


class SummerTests(unittest.TestCase):
    def test_cheapest_hours_medium_rest_low(self):
        s = Settings(daily_hours=4)
        cheap = decide(Inputs(now=at(2), pool_temp=24, outdoor_temp=18, prices=PRICES), s)
        dear = decide(Inputs(now=at(18), pool_temp=24, outdoor_temp=18, prices=PRICES), s)
        self.assertEqual((cheap.mode, dear.mode), (MEDIUM, LOW))

    def test_algae_skip_and_buffer(self):
        s = Settings()
        self.assertEqual(decide(Inputs(now=at(12), pool_temp=6, outdoor_temp=10), s).mode, OFF)
        self.assertEqual(decide(Inputs(now=at(12), pool_temp=6, outdoor_temp=3), s).mode, LOW)

    def test_must_run_respects_peak(self):
        s = Settings(daily_hours=8)
        d = decide(Inputs(now=at(18), pool_temp=24, outdoor_temp=18, prices=PRICES,
                          is_peak=True, hours_run=0), s)
        self.assertEqual((d.mode, d.rule), (LOW, logic.RULE_MUST_RUN_PEAK))

    def test_manual_does_not_enforce(self):
        d = decide(Inputs(now=at(12), pool_temp=24, outdoor_temp=18, current_mode=HIGH,
                          automation_enabled=False), Settings())
        self.assertEqual((d.mode, d.enforce), (HIGH, False))


class PriceTests(unittest.TestCase):
    def test_plain_24(self):
        self.assertEqual(prices_mod.extract_today_prices({"today": PRICES}, date(2026, 11, 13)), PRICES)

    def test_quarter_hours_are_averaged(self):
        raw = [v for p in PRICES for v in (p, p, p, p)]
        out = prices_mod.extract_today_prices({"today": raw}, date(2026, 11, 13))
        self.assertEqual(len(out), 24)
        self.assertAlmostEqual(out[18], 1.80)

    def test_dict_list_filters_today_and_buckets(self):
        raw = [{"start_time": f"2026-11-13T{h:02d}:{m:02d}:00+01:00", "price": PRICES[h]}
               for h in range(24) for m in (0, 15, 30, 45)]
        raw += [{"start_time": "2026-11-14T00:00:00+01:00", "price": 99.0}]
        out = prices_mod.extract_today_prices({"prices": raw}, date(2026, 11, 13))
        self.assertAlmostEqual(out[0], PRICES[0])
        self.assertNotIn(99.0, out)


if __name__ == "__main__":
    unittest.main()

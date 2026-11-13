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
        d = decide(Inputs(now=at(18), season=WINTER, pool_temp=3.8, outdoor_temp=-3,
                          prices=PRICES, automation_enabled=False), Settings())
        self.assertEqual(d.mode, MEDIUM)
        self.assertEqual(d.safety, logic.SAFETY_POOL_CRITICAL)
        self.assertTrue(d.enforce)

    def test_pool_critical_low_unless_freezing_outside(self):
        s = Settings()
        for outdoor, mode in ((10, LOW), (3, LOW), (0, LOW), (-0.5, MEDIUM), (-8, MEDIUM)):
            d = decide(Inputs(now=at(12), season=WINTER, pool_temp=3.5, outdoor_temp=outdoor), s)
            self.assertEqual((outdoor, d.mode), (outdoor, mode))
        self.assertEqual(decide(Inputs(now=at(12), season=WINTER, pool_temp=3.5,
                                       outdoor_temp=None), s).mode, MEDIUM)

    def test_pool_critical_medium_hysteresis(self):
        s = Settings()
        held = decide(Inputs(now=at(12), season=WINTER, pool_temp=3.5, outdoor_temp=0.3,
                             current_mode=MEDIUM, prev_safety=logic.SAFETY_POOL_CRITICAL), s)
        self.assertEqual(held.mode, MEDIUM)
        released = decide(Inputs(now=at(12), season=WINTER, pool_temp=3.5, outdoor_temp=0.6,
                                  current_mode=MEDIUM, prev_safety=logic.SAFETY_POOL_CRITICAL), s)
        self.assertEqual(released.mode, LOW)

    def test_pool_critical_in_summer_too(self):
        d = decide(Inputs(now=at(18), season=SUMMER, pool_temp=3.5, outdoor_temp=10), Settings())
        self.assertEqual((d.mode, d.safety), (LOW, logic.SAFETY_POOL_CRITICAL))

    def test_pool_critical_hysteresis(self):
        s = Settings()
        held = decide(Inputs(now=at(18), season=WINTER, pool_temp=4.3, outdoor_temp=8,
                             prices=PRICES, prev_safety=logic.SAFETY_POOL_CRITICAL), s)
        self.assertEqual(held.safety, logic.SAFETY_POOL_CRITICAL)
        released = decide(Inputs(now=at(18), season=WINTER, pool_temp=4.6, outdoor_temp=8,
                                 prices=PRICES, prev_safety=logic.SAFETY_POOL_CRITICAL), s)
        self.assertIsNone(released.safety)
        self.assertEqual(released.mode, OFF)

    def test_frost_guard_runs_low_with_cold_pool(self):
        d = decide(Inputs(now=at(18), season=WINTER, pool_temp=6, outdoor_temp=-3,
                          prices=PRICES), Settings())
        self.assertEqual((d.mode, d.safety), (LOW, logic.SAFETY_FROST))
        self.assertEqual(d.plan, [LOW] * 24)

    def test_frost_guard_can_be_set_low(self):
        d = decide(Inputs(now=at(18), season=WINTER, pool_temp=6, outdoor_temp=-3),
                   Settings(freeze=-10))
        self.assertEqual((d.mode, d.safety), (OFF, None))

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
    """Winter runs by temperature only: price and time of day don't matter."""

    def test_warm_pool_runs_low_all_day_at_any_price(self):
        for h in range(24):
            d = decide(Inputs(now=at(h), season=WINTER, pool_temp=8, outdoor_temp=6,
                              prices=PRICES), Settings())
            self.assertEqual((h, d.mode, d.tier, d.rule), (h, LOW, logic.TIER_ALGAE, logic.RULE_WINTER))
        self.assertEqual(d.plan, [LOW] * 24)
        self.assertIsNone(d.target_hours)

    def test_cold_outside_with_warm_pool_still_low(self):
        d = decide(Inputs(now=at(18), season=WINTER, pool_temp=7.5, outdoor_temp=3,
                          prices=PRICES), Settings())
        self.assertEqual(d.mode, LOW)

    def test_pool_between_critical_and_algae_is_off(self):
        for outdoor in (3, 10):
            d = decide(Inputs(now=at(2), season=WINTER, pool_temp=5.5, outdoor_temp=outdoor,
                              prices=PRICES), Settings())
            self.assertEqual((d.mode, d.tier), (OFF, logic.TIER_REST))
        self.assertEqual(d.plan, [OFF] * 24)

    def test_algae_limit_hysteresis(self):
        s = Settings()
        held = decide(Inputs(now=at(12), season=WINTER, pool_temp=6.7, outdoor_temp=6,
                             current_mode=LOW), s)
        self.assertEqual(held.mode, LOW)
        stopped = decide(Inputs(now=at(12), season=WINTER, pool_temp=6.4, outdoor_temp=6,
                                current_mode=LOW), s)
        self.assertEqual(stopped.mode, OFF)
        not_started = decide(Inputs(now=at(12), season=WINTER, pool_temp=6.7, outdoor_temp=6), s)
        self.assertEqual(not_started.mode, OFF)

    def test_winter_without_prices(self):
        self.assertEqual(decide(Inputs(now=at(12), season=WINTER, pool_temp=8, outdoor_temp=3),
                                Settings()).mode, LOW)
        self.assertEqual(decide(Inputs(now=at(12), season=WINTER, pool_temp=5, outdoor_temp=8),
                                Settings()).mode, OFF)

    def test_winter_respects_cooldown(self):
        d = decide(Inputs(now=at(12), season=WINTER, pool_temp=8, outdoor_temp=6,
                          in_cooldown=True), Settings())
        self.assertEqual((d.mode, d.rule), (OFF, logic.RULE_COOLDOWN))


class SummerTests(unittest.TestCase):
    def test_cheapest_hours_medium_rest_low(self):
        s = Settings(daily_hours=4)
        cheap = decide(Inputs(now=at(2), pool_temp=24, outdoor_temp=18, prices=PRICES), s)
        dear = decide(Inputs(now=at(18), pool_temp=24, outdoor_temp=18, prices=PRICES), s)
        self.assertEqual((cheap.mode, dear.mode), (MEDIUM, LOW))

    def test_algae_skip(self):
        s = Settings()
        self.assertEqual(decide(Inputs(now=at(12), pool_temp=6, outdoor_temp=10), s).mode, OFF)
        self.assertEqual(decide(Inputs(now=at(12), pool_temp=6, outdoor_temp=3), s).mode, OFF)
        self.assertEqual(decide(Inputs(now=at(12), pool_temp=6, outdoor_temp=1), s).mode, LOW)  # frost guard

    def test_must_run_respects_peak(self):
        s = Settings(daily_hours=8)
        d = decide(Inputs(now=at(18), pool_temp=24, outdoor_temp=18, prices=PRICES,
                          hours_run=0), s)
        self.assertEqual((d.mode, d.rule), (LOW, logic.RULE_MUST_RUN_PEAK))

    def test_manual_does_not_enforce(self):
        d = decide(Inputs(now=at(12), pool_temp=24, outdoor_temp=18, current_mode=HIGH,
                          automation_enabled=False), Settings())
        self.assertEqual((d.mode, d.enforce), (HIGH, False))


    def test_summer_without_prices_runs_until_target(self):
        s = Settings(daily_hours=4)
        self.assertEqual(decide(Inputs(now=at(9), pool_temp=24, outdoor_temp=18), s).mode, MEDIUM)
        self.assertEqual(decide(Inputs(now=at(9), pool_temp=24, outdoor_temp=18, hours_run=4), s).mode, LOW)

    def test_peak_hours_come_from_prices(self):
        self.assertTrue(logic.is_peak_hour(PRICES, 18))
        self.assertTrue(logic.is_peak_hour(PRICES, 8))
        self.assertFalse(logic.is_peak_hour(PRICES, 2))
        self.assertFalse(logic.is_peak_hour(None, 18))


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


class SpeedTests(unittest.TestCase):
    def test_classify_speed(self):
        c = logic.classify_speed
        self.assertIsNone(c(None, 10, 300, 500))
        self.assertEqual(c(5, 10, 300, 500), "off")
        self.assertEqual(c(180, 10, 300, 500), "low")
        self.assertEqual(c(300, 10, 300, 500), "medium")
        self.assertEqual(c(620, 10, 300, 500), "high")

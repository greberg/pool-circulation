"""Circulation decision logic — pure Python, no Home Assistant imports.

The coordinator gathers sensor values into an ``Inputs`` snapshot and calls
``decide()``. Everything here is deterministic and unit-testable.

Seasons
-------
Summer — filtration + heat pump. Cheapest ``daily_hours`` at MEDIUM, the pump
never stops while the pool is warm enough for algae (≥ algae threshold).

Winter — heat pump left alone, circulation only to stay safe and clean:
  cold   outdoor ≤ buffer (default 4 °C): run LOW all day except the most
         expensive hours, never two skipped hours in a row
  algae  outdoor mild, pool ≥ algae threshold (default 7 °C): cheapest
         ``winter_algae_hours`` at LOW
  rest   outdoor mild, pool cold: cheapest ``winter_min_hours`` at LOW, just to
         keep water moving and the pool sensor reading real water

Safety guards (both seasons, override price and automation-off)
---------------------------------------------------------------
pool_critical  pool ≤ critical temp (default 4 °C) → MEDIUM continuously
frost          outdoor ≤ freeze threshold (default 2 °C) → LOW continuously
sensor_failsafe winter with no outdoor reading → LOW continuously
Both temperature guards release only once the reading is ``hysteresis`` above
the threshold, so the pump does not flap around the limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .const import MODE_HIGH, MODE_LOW, MODE_MEDIUM, MODE_OFF, PEAK_HOURS, SEASON_SUMMER, SEASON_WINTER

INF = float("inf")
RUNNING = (MODE_LOW, MODE_MEDIUM, MODE_HIGH)

# Safety states
SAFETY_POOL_CRITICAL = "pool_critical"
SAFETY_FROST = "frost"
SAFETY_SENSOR_FAILSAFE = "sensor_failsafe"

# Winter tiers
TIER_COLD = "cold"
TIER_ALGAE = "algae"
TIER_REST = "rest"

# Rule keys — which step decided the mode (shown on cards / attributes)
RULE_EXTRA_FILTER = "extra_filter"
RULE_COVER_BURST = "cover_burst"
RULE_MANUAL = "manual"
RULE_MIN_ON = "min_on"
RULE_ALGAE_SKIP = "algae_skip"
RULE_OUTDOOR_BUFFER = "outdoor_buffer"
RULE_MUST_RUN = "must_run"
RULE_MUST_RUN_PEAK = "must_run_peak"
RULE_SCHEDULE = "schedule"
RULE_REACTIVE = "reactive"
RULE_COOLDOWN = "cooldown"
RULE_MIN_LOW = "min_low"


@dataclass
class Settings:
    pool_critical: float = 4.0
    hysteresis: float = 0.5
    algae: float = 7.0
    freeze: float = 2.0
    buffer: float = 4.0
    daily_hours: int = 8
    winter_skip_hours: int = 4
    winter_algae_hours: int = 4
    winter_min_hours: int = 2


@dataclass
class Inputs:
    now: datetime
    season: str = SEASON_SUMMER
    pool_temp: float | None = None
    outdoor_temp: float | None = None
    prices: list[float] | None = None      # 24 hourly prices for today
    current_mode: str = MODE_OFF
    automation_enabled: bool = True
    extra_filter: bool = False
    cover_burst: bool = False
    in_min_on: bool = False
    in_cooldown: bool = False
    hours_run: float = 0.0                 # MEDIUM + HIGH hours today
    hours_low: float = 0.0                 # LOW hours today
    prev_safety: str | None = None         # for hysteresis


@dataclass
class Decision:
    mode: str
    rule: str
    reason: str
    enforce: bool = True                   # False → leave devices as they are
    safety: str | None = None
    tier: str | None = None
    plan: list[str] | None = None          # planned mode per hour today
    target_hours: float | None = None
    done_hours: float = 0.0
    must_run: bool = False
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def safety_state(inp: Inputs, s: Settings) -> str | None:
    """Return the active safety guard, honouring hysteresis on release."""
    if inp.pool_temp is not None:
        limit = s.pool_critical + (s.hysteresis if inp.prev_safety == SAFETY_POOL_CRITICAL else 0)
        if inp.pool_temp <= limit:
            return SAFETY_POOL_CRITICAL
    if inp.outdoor_temp is not None:
        limit = s.freeze + (s.hysteresis if inp.prev_safety == SAFETY_FROST else 0)
        if inp.outdoor_temp <= limit:
            return SAFETY_FROST
    elif inp.season == SEASON_WINTER:
        return SAFETY_SENSOR_FAILSAFE
    return None


SAFETY_MODE = {
    SAFETY_POOL_CRITICAL: MODE_MEDIUM,
    SAFETY_FROST: MODE_LOW,
    SAFETY_SENSOR_FAILSAFE: MODE_LOW,
}


def winter_tier(inp: Inputs, s: Settings) -> str:
    if inp.outdoor_temp is not None and inp.outdoor_temp <= s.buffer:
        return TIER_COLD
    # Unknown pool temp → assume algae is possible (circulate more, not less)
    if inp.pool_temp is None or inp.pool_temp >= s.algae:
        return TIER_ALGAE
    return TIER_REST


def cheapest_hours(prices: list[float], n: int, exclude: set[int] | None = None) -> set[int]:
    exclude = exclude or set()
    ranked = sorted((h for h in range(24) if h not in exclude), key=lambda h: prices[h])
    return set(ranked[:max(0, n)])


def expensive_skips(prices: list[float], k: int) -> set[int]:
    """Pick up to *k* most expensive hours to skip, never two in a row.

    Hours with unknown price (∞) are never skipped — when in doubt, run.
    """
    skipped: set[int] = set()
    for h in sorted((h for h in range(24) if prices[h] < INF), key=lambda h: -prices[h]):
        if len(skipped) >= k:
            break
        if (h - 1) in skipped or (h + 1) in skipped:
            continue
        skipped.add(h)
    return skipped


def is_peak_hour(prices: list[float] | None, hour: int) -> bool:
    """True when *hour* is among today's PEAK_HOURS most expensive hours."""
    if not prices or len(prices) < 24 or prices[hour] == INF:
        return False
    known = sorted((p for p in prices if p < INF), reverse=True)
    return prices[hour] >= known[min(PEAK_HOURS, len(known)) - 1]


def summer_algae_skip(inp: Inputs, s: Settings) -> bool:
    return inp.pool_temp is not None and inp.pool_temp < s.algae


def outdoor_buffer_active(inp: Inputs, s: Settings) -> bool:
    return inp.outdoor_temp is not None and s.freeze < inp.outdoor_temp <= s.buffer


def plan_today(inp: Inputs, s: Settings, safety: str | None, tier: str | None) -> list[str] | None:
    """Planned mode for each hour of today under current conditions.

    Returns None when the plan depends on prices that are not available.
    """
    if safety:
        return [SAFETY_MODE[safety]] * 24

    if inp.season == SEASON_SUMMER and summer_algae_skip(inp, s):
        return [MODE_LOW if outdoor_buffer_active(inp, s) else MODE_OFF] * 24

    prices = inp.prices
    if not prices or len(prices) < 24:
        return None

    if inp.season == SEASON_SUMMER:
        medium = cheapest_hours(prices, s.daily_hours)
        # Pool warm → never off, so every non-MEDIUM hour is LOW
        return [MODE_MEDIUM if h in medium else MODE_LOW for h in range(24)]

    if tier == TIER_COLD:
        skipped = expensive_skips(prices, s.winter_skip_hours)
        return [MODE_OFF if h in skipped else MODE_LOW for h in range(24)]
    n = s.winter_algae_hours if tier == TIER_ALGAE else s.winter_min_hours
    run = cheapest_hours(prices, n)
    return [MODE_LOW if h in run else MODE_OFF for h in range(24)]


def _fmt(t: float | None) -> str:
    return "?" if t is None else f"{t:.1f}°C"


# ---------------------------------------------------------------------------
# Main decision
# ---------------------------------------------------------------------------
def decide(inp: Inputs, s: Settings) -> Decision:
    winter = inp.season == SEASON_WINTER
    safety = safety_state(inp, s)
    tier = winter_tier(inp, s) if winter and not safety else None
    plan = plan_today(inp, s, safety, tier)
    hour = inp.now.hour
    hour_frac = inp.now.minute / 60

    # Daily target bookkeeping
    if winter:
        target = {TIER_ALGAE: s.winter_algae_hours, TIER_REST: s.winter_min_hours}.get(tier)
        done = inp.hours_run + inp.hours_low
        counted = RUNNING
    else:
        target = s.daily_hours
        done = inp.hours_run
        counted = (MODE_MEDIUM, MODE_HIGH)

    def result(mode: str, rule: str, reason: str, enforce: bool = True, must_run: bool = False) -> Decision:
        return Decision(
            mode=mode, rule=rule, reason=reason, enforce=enforce, safety=safety,
            tier=tier, plan=plan, target_hours=target, done_hours=round(done, 2),
            must_run=must_run,
        )

    # 1–2. User-triggered bursts — run faster than any guard, so safe to put first
    if inp.extra_filter:
        return result(MODE_HIGH, RULE_EXTRA_FILTER, "Extra filter running at high speed")
    if inp.cover_burst:
        return result(MODE_HIGH, RULE_COVER_BURST, "Cover opened — 5 min high-speed burst")

    # 3. Safety guards — ignore price, timers and automation-off
    if safety == SAFETY_POOL_CRITICAL:
        return result(MODE_MEDIUM, safety,
                      f"Pool water {_fmt(inp.pool_temp)} ≤ {_fmt(s.pool_critical)} — "
                      "continuous circulation to prevent freezing")
    if safety == SAFETY_FROST:
        return result(MODE_LOW, safety,
                      f"Frost: outdoor {_fmt(inp.outdoor_temp)} ≤ {_fmt(s.freeze)} — "
                      "continuous low-speed circulation")
    if safety == SAFETY_SENSOR_FAILSAFE:
        return result(MODE_LOW, safety,
                      "Winter and no outdoor temperature — running low speed to be safe")

    # 4. Automation off — hold whatever the devices are doing
    if not inp.automation_enabled:
        return result(inp.current_mode, RULE_MANUAL, "Automation off — manual control", enforce=False)

    # 5. Minimum on-time
    if inp.current_mode != MODE_OFF and inp.in_min_on:
        return result(inp.current_mode, RULE_MIN_ON, "Holding until minimum on-time has passed")

    # 6. Summer algae gate — cold pool needs no filtration
    if not winter and summer_algae_skip(inp, s):
        if outdoor_buffer_active(inp, s):
            return result(MODE_LOW, RULE_OUTDOOR_BUFFER,
                          f"Pool {_fmt(inp.pool_temp)} below algae limit, outdoor "
                          f"{_fmt(inp.outdoor_temp)} near frost — low speed")
        return result(MODE_OFF, RULE_ALGAE_SKIP,
                      f"Pool {_fmt(inp.pool_temp)} below algae limit {_fmt(s.algae)} — no filtration needed")

    # 7. Must-run — the rest of the plan cannot cover what is still needed today
    must_run = False
    if target is not None:
        needed = max(0.0, target - done)
        future = 0.0
        if plan:
            for h in range(hour, 24):
                if plan[h] in counted:
                    future += (1 - hour_frac) if h == hour else 1
        remaining = 24 - hour - hour_frac
        gap = needed - future
        must_run = gap > 0.01 and gap >= remaining - 0.01

    if must_run:
        if winter:
            desired, rule, reason = MODE_LOW, RULE_MUST_RUN, "Catching up on today's winter circulation hours"
        elif is_peak_hour(inp.prices, hour):
            desired, rule, reason = (MODE_LOW, RULE_MUST_RUN_PEAK,
                                     "Behind on daily hours but peak price — low speed only")
        else:
            desired, rule, reason = MODE_MEDIUM, RULE_MUST_RUN, "Catching up on today's filtration hours"
    elif plan is not None:
        desired = plan[hour]
        rule = RULE_SCHEDULE
        if winter:
            label = {
                TIER_COLD: f"Winter, cold outdoor {_fmt(inp.outdoor_temp)}: low speed, skipping priciest hours",
                TIER_ALGAE: f"Winter, pool {_fmt(inp.pool_temp)} ≥ algae limit: cheapest {s.winter_algae_hours} h",
                TIER_REST: f"Winter rest: cheapest {s.winter_min_hours} h to keep water moving",
            }[tier]
            reason = f"{label} — {'running' if desired != MODE_OFF else 'off'} this hour"
        else:
            reason = ("Cheap hour — daily filtration at medium speed" if desired == MODE_MEDIUM
                      else "Outside cheapest hours — low speed")
    else:
        # No price list today — run until today's hours are done
        rule = RULE_REACTIVE
        needed = max(0.0, (target or 0) - done)
        if winter:
            desired = MODE_LOW if tier == TIER_COLD or needed > 0 else MODE_OFF
        else:
            desired = MODE_MEDIUM if needed > 0 else MODE_LOW
        reason = "No price list — running until today's hours are done"

    # 8. Cooldown — don't restart right after stopping
    if desired != MODE_OFF and inp.current_mode == MODE_OFF and inp.in_cooldown:
        desired, rule, reason = MODE_OFF, RULE_COOLDOWN, "Waiting for cooldown before restarting"

    # 9. Summer: warm pool never stands still
    if not winter and desired == MODE_OFF:
        desired, rule, reason = MODE_LOW, RULE_MIN_LOW, "Pool warm — never fully off, low speed"

    return result(desired, rule, reason, must_run=must_run)

"""Circulation decision logic — pure Python, no Home Assistant imports.

The coordinator gathers sensor values into an ``Inputs`` snapshot and calls
``decide()``. Everything here is deterministic and unit-testable.

Seasons
-------
Summer — filtration + heat pump. Cheapest ``daily_hours`` at MEDIUM, the pump
never stops while the pool is warm enough for algae (≥ algae threshold).
Below the algae threshold it is off.

Winter — heat pump left alone, price ignored, circulation by temperature only:
  algae  pool ≥ algae threshold (default 7 °C) → LOW around the clock
  rest   pool between the critical temp and the algae threshold → OFF

Safety guards (both seasons, override price, timers and automation-off)
-----------------------------------------------------------------------
pool_critical   pool ≤ critical temp (default 4 °C) → LOW, or MEDIUM when
                outdoor is below ``outdoor_medium`` (default 0 °C)
frost           outdoor ≤ freeze threshold (default 2 °C) → LOW, protects
                pipes, pump and filter while the pool itself is not at risk
sensor_failsafe winter with no outdoor reading → LOW
Every limit has ``hysteresis`` so the pump does not flap around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .const import MODE_HIGH, MODE_LOW, MODE_MEDIUM, MODE_OFF, PEAK_HOURS, SEASON_SUMMER, SEASON_WINTER

INF = float("inf")

# Safety states
SAFETY_POOL_CRITICAL = "pool_critical"
SAFETY_FROST = "frost"
SAFETY_SENSOR_FAILSAFE = "sensor_failsafe"

# Winter tiers
TIER_ALGAE = "algae"
TIER_REST = "rest"

# Rule keys — which step decided the mode (shown on cards / attributes)
RULE_EXTRA_FILTER = "extra_filter"
RULE_COVER_BURST = "cover_burst"
RULE_MANUAL = "manual"
RULE_MIN_ON = "min_on"
RULE_ALGAE_SKIP = "algae_skip"
RULE_WINTER = "winter"
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
    outdoor_medium: float = 0.0            # pool critical: MEDIUM below this outdoor temp
    daily_hours: int = 8


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


def safety_mode(inp: Inputs, s: Settings, safety: str) -> str:
    """Speed for an active safety guard."""
    if safety != SAFETY_POOL_CRITICAL:
        return MODE_LOW
    if inp.outdoor_temp is None:
        return MODE_MEDIUM                 # can't tell how cold it is — be safe
    # Stay at MEDIUM until outdoor is hysteresis above the limit
    limit = s.outdoor_medium + (s.hysteresis if inp.current_mode == MODE_MEDIUM else 0)
    return MODE_MEDIUM if inp.outdoor_temp < limit else MODE_LOW


def winter_tier(inp: Inputs, s: Settings) -> str:
    # Unknown pool temp → assume algae is possible (circulate more, not less)
    if inp.pool_temp is None:
        return TIER_ALGAE
    # Keep running until the pool is hysteresis below the threshold
    limit = s.algae - (s.hysteresis if inp.current_mode != MODE_OFF else 0)
    return TIER_ALGAE if inp.pool_temp >= limit else TIER_REST


def cheapest_hours(prices: list[float], n: int, exclude: set[int] | None = None) -> set[int]:
    exclude = exclude or set()
    ranked = sorted((h for h in range(24) if h not in exclude), key=lambda h: prices[h])
    return set(ranked[:max(0, n)])


def is_peak_hour(prices: list[float] | None, hour: int) -> bool:
    """True when *hour* is among today's PEAK_HOURS most expensive hours."""
    if not prices or len(prices) < 24 or prices[hour] == INF:
        return False
    known = sorted((p for p in prices if p < INF), reverse=True)
    return prices[hour] >= known[min(PEAK_HOURS, len(known)) - 1]


def summer_algae_skip(inp: Inputs, s: Settings) -> bool:
    return inp.pool_temp is not None and inp.pool_temp < s.algae


def plan_today(inp: Inputs, s: Settings, safety: str | None, tier: str | None) -> list[str] | None:
    """Planned mode for each hour of today under current conditions.

    Returns None when the plan depends on prices that are not available.
    """
    if safety:
        return [safety_mode(inp, s, safety)] * 24

    if inp.season == SEASON_WINTER:
        return [MODE_LOW if tier == TIER_ALGAE else MODE_OFF] * 24

    if summer_algae_skip(inp, s):
        return [MODE_OFF] * 24

    prices = inp.prices
    if not prices or len(prices) < 24:
        return None

    medium = cheapest_hours(prices, s.daily_hours)
    # Pool warm → never off, so every non-MEDIUM hour is LOW
    return [MODE_MEDIUM if h in medium else MODE_LOW for h in range(24)]


def classify_speed(
    value: float | None, running_above: float, medium_from: float, high_from: float
) -> str | None:
    """Measured pump speed from a feedback reading (power or rpm).

    None when there is no reading. OFF at or below *running_above*.
    """
    if value is None:
        return None
    if value <= running_above:
        return MODE_OFF
    if value >= high_from:
        return MODE_HIGH
    if value >= medium_from:
        return MODE_MEDIUM
    return MODE_LOW


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

    # Daily target bookkeeping — summer only, winter runs by temperature
    if winter:
        target = None
        done = inp.hours_run + inp.hours_low
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
        mode = safety_mode(inp, s, safety)
        cold = (f"outdoor {_fmt(inp.outdoor_temp)} below {_fmt(s.outdoor_medium)}"
                if mode == MODE_MEDIUM else f"outdoor {_fmt(inp.outdoor_temp)}")
        return result(mode, safety,
                      f"Pool water {_fmt(inp.pool_temp)} ≤ {_fmt(s.pool_critical)}, {cold} — "
                      f"continuous {mode} speed to prevent freezing")
    if safety == SAFETY_FROST:
        return result(MODE_LOW, safety,
                      f"Frost guard: outdoor {_fmt(inp.outdoor_temp)} ≤ {_fmt(s.freeze)} — "
                      "low speed to keep pipes and pump from freezing")
    if safety == SAFETY_SENSOR_FAILSAFE:
        return result(MODE_LOW, safety,
                      "Winter and no outdoor temperature — running low speed to be safe")

    # 4. Automation off — hold whatever the devices are doing
    if not inp.automation_enabled:
        return result(inp.current_mode, RULE_MANUAL, "Automation off — manual control", enforce=False)

    # 5. Minimum on-time
    if inp.current_mode != MODE_OFF and inp.in_min_on:
        return result(inp.current_mode, RULE_MIN_ON, "Holding until minimum on-time has passed")

    # 6. Winter — by pool temperature only
    if winter:
        if tier == TIER_ALGAE:
            desired = MODE_LOW
            reason = (f"Winter, pool {_fmt(inp.pool_temp)} ≥ algae limit {_fmt(s.algae)} — "
                      "low speed around the clock")
        else:
            desired = MODE_OFF
            reason = (f"Winter, pool {_fmt(inp.pool_temp)} between {_fmt(s.pool_critical)} and "
                      f"{_fmt(s.algae)} — no algae, no freezing risk: off")
        if desired != MODE_OFF and inp.current_mode == MODE_OFF and inp.in_cooldown:
            return result(MODE_OFF, RULE_COOLDOWN, "Waiting for cooldown before restarting")
        return result(desired, RULE_WINTER, reason)

    # 7. Summer algae gate — cold pool needs no filtration
    if summer_algae_skip(inp, s):
        return result(MODE_OFF, RULE_ALGAE_SKIP,
                      f"Pool {_fmt(inp.pool_temp)} below algae limit {_fmt(s.algae)} — no filtration needed")

    # 8. Must-run — the rest of the plan cannot cover what is still needed today
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
        if is_peak_hour(inp.prices, hour):
            desired, rule, reason = (MODE_LOW, RULE_MUST_RUN_PEAK,
                                     "Behind on daily hours but peak price — low speed only")
        else:
            desired, rule, reason = MODE_MEDIUM, RULE_MUST_RUN, "Catching up on today's filtration hours"
    elif plan is not None:
        desired = plan[hour]
        rule = RULE_SCHEDULE
        reason = ("Cheap hour — daily filtration at medium speed" if desired == MODE_MEDIUM
                  else "Outside cheapest hours — low speed")
    else:
        # No price list today — run until today's hours are done
        rule = RULE_REACTIVE
        needed = max(0.0, (target or 0) - done)
        desired = MODE_MEDIUM if needed > 0 else MODE_LOW
        reason = "No price list — running until today's hours are done"

    # 9. Cooldown — don't restart right after stopping
    if desired != MODE_OFF and inp.current_mode == MODE_OFF and inp.in_cooldown:
        desired, rule, reason = MODE_OFF, RULE_COOLDOWN, "Waiting for cooldown before restarting"

    # 10. Summer: warm pool never stands still
    if desired == MODE_OFF:
        desired, rule, reason = MODE_LOW, RULE_MIN_LOW, "Pool warm — never fully off, low speed"

    return result(desired, rule, reason, must_run=must_run)

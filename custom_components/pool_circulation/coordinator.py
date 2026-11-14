"""Coordinator for Pool Circulation integration.

Owns all state and talks to Home Assistant. The actual decision of which mode
to run is made by the pure ``logic.decide()`` — this class gathers inputs,
applies the result to the devices and keeps everything consistent across
restarts:

- Every minute (and immediately on relevant sensor changes) the mode is
  re-decided and the devices are *reconciled*: each switch is compared with
  the wanted state and only mismatches are commanded. A pump controller that
  reboots and comes back "off" is turned back on within a minute.
- Runtime is accounted from elapsed time while the pump switch is actually on,
  persisted every few minutes. Time while HA was down is never counted.
- Extra filter and cover burst are stored as end timestamps, so they survive
  a restart and simply expire on time.
- Temperature safety guards (hysteresis state), season and last good price
  list are persisted too.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE_HEAT_PUMP,
    CONF_COOLDOWN_MINUTES,
    CONF_COVER_POOL,
    CONF_DAILY_HOURS,
    CONF_EXTRA_FILTER_DURATION,
    CONF_FEEDBACK_HIGH_FROM,
    CONF_FEEDBACK_MEDIUM_FROM,
    CONF_FEEDBACK_THRESHOLD,
    CONF_HP_TEMP_BEST_PRICE,
    CONF_HP_TEMP_NORMAL,
    CONF_MIN_ON_MINUTES,
    CONF_POOL_CRITICAL_TEMP,
    CONF_SENSOR_OUTDOOR_TEMP,
    CONF_SENSOR_POOL_TEMP,
    CONF_SENSOR_PUMP_FEEDBACK,
    CONF_SENSOR_PUMP_POWER,
    CONF_SENSOR_PUMP_RPM,
    CONF_SENSOR_PRICE,
    CONF_SWITCH_CIRCULATION,
    CONF_SWITCH_RPM_HIGH,
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    CONF_SWITCH_UV_LAMP,
    CONF_TEMP_ALGAE_THRESHOLD,
    CONF_TEMP_FREEZE_THRESHOLD,
    CONF_TEMP_OUTDOOR_MEDIUM,
    COVER_BURST_MINUTES,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_DAILY_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    AUTO_SPEED_BANDS,
    DEFAULT_FEEDBACK_THRESHOLD,
    DEFAULT_HP_TEMP_BEST_PRICE,
    DEFAULT_HP_TEMP_NORMAL,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_POOL_CRITICAL_TEMP,
    DEFAULT_SAFETY_HYSTERESIS,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
    DEFAULT_TEMP_OUTDOOR_MEDIUM,
    DOMAIN,
    EVENT_EXTRA_FILTER_CHANGED,
    EVENT_MODE_CHANGED,
    EVENT_PUMP_FAULT,
    EVENT_UV_CHANGED,
    FAULT_RETRY_MINUTES,
    FEEDBACK_GRACE_SECONDS,
    SPEED_SETTLE_SECONDS,
    MAX_ACCOUNTING_GAP_SECONDS,
    MODE_HIGH,
    MODE_LOW,
    MODE_MEDIUM,
    MODE_OFF,
    SAVE_INTERVAL_MINUTES,
    SEASON_SUMMER,
    SEASON_WINTER,
    STORE_KEY,
    STORE_VERSION,
)
from .logic import INF, Decision, Inputs, Settings, classify_speed, decide, is_peak_hour
from .prices import describe_attrs, extract_today_prices

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE = ("unavailable", "unknown", "")
# Give temperature sensors this long after HA start before deciding without them
_STARTUP_GRACE_SECONDS = 120

# Entities of this integration the cards need, by unique_id suffix
_OWN_ENTITIES = {
    "mode": ("sensor", "mode"),
    "reason": ("sensor", "reason"),
    "winter_mode": ("switch", "winter_mode"),
    "automation": ("switch", "automation"),
    "extra_filter": ("switch", "extra_filter"),
    "pump_fault": ("binary_sensor", "pump_fault"),
}


def _parse_ts(raw: str | None) -> datetime | None:
    """Parse a stored timestamp; naive values (pre-1.6 storage) are local time."""
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return ts


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class PoolCirculationCoordinator(DataUpdateCoordinator):
    """Manage pool circulation and heat pump based on season, temperature and price."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.entry = entry
        self._store = Store(hass, STORE_VERSION, f"{STORE_KEY}_{entry.entry_id}")
        self._lock = asyncio.Lock()
        self._unsubs: list[Any] = []
        self._started_unsub: Any = None

        # Persisted state
        self.automation_enabled: bool = True
        self.winter_mode: bool = False
        self.current_mode: str = MODE_OFF
        self._day: date = dt_util.now().date()
        self._minutes_run: float = 0.0     # MEDIUM + HIGH minutes today
        self._minutes_low: float = 0.0     # LOW minutes today
        self._energy_wh: float = 0.0       # pump energy today, from a power feedback sensor
        self._cost: float = 0.0            # energy × price today, in the price sensor's currency
        self._last_turned_on: datetime | None = None
        self._last_turned_off: datetime | None = None
        self._extra_filter_until: datetime | None = None
        self._cover_burst_until: datetime | None = None
        self._safety: str | None = None
        self._prices: list[float] | None = None
        self._prices_day: date | None = None

        # Runtime only
        self._ready = False
        self._started_at: datetime | None = None
        self._acct_ts: datetime | None = None
        self._last_save: datetime | None = None
        self._decision: Decision | None = None
        self._failed_entities: set[str] = set()
        self._price_warned_day: date | None = None
        self._entity_ids: dict[str, str | None] = {}

        # Pump feedback — does the pump actually do what the switches say?
        self.pump_fault: str | None = None          # "not_running" / "not_stopping" / "wrong_speed"
        # When the speed was last changed — the controller needs a few minutes
        # to settle. Starts "now" so a restart doesn't flag a ramping pump.
        self._speed_since: datetime = dt_util.now()
        self._last_actual: str | None = None
        self._fault_since: datetime | None = None
        self._mismatch_since: datetime | None = None
        self._last_retry: datetime | None = None

        # Config as of the last options update, so the listener can tell
        # whether an entity changed (reload) or only a setting (re-decide)
        self.cfg_snapshot: dict = self.cfg

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    @property
    def cfg(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def season(self) -> str:
        return SEASON_WINTER if self.winter_mode else SEASON_SUMMER

    @property
    def daily_hours_target(self) -> int:
        return int(self.cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS))

    @property
    def extra_filter_duration(self) -> int:
        return int(self.cfg.get(CONF_EXTRA_FILTER_DURATION, DEFAULT_EXTRA_FILTER_DURATION))

    @property
    def hours_run_today(self) -> float:
        return round(self._minutes_run / 60, 2)

    @property
    def hours_low_today(self) -> float:
        return round(self._minutes_low / 60, 2)

    @property
    def extra_filter_active(self) -> bool:
        return self._extra_filter_until is not None and dt_util.now() < self._extra_filter_until

    @property
    def cover_circulation_active(self) -> bool:
        return self._cover_burst_until is not None and dt_util.now() < self._cover_burst_until

    def _settings(self) -> Settings:
        c = self.cfg
        return Settings(
            pool_critical=float(c.get(CONF_POOL_CRITICAL_TEMP, DEFAULT_POOL_CRITICAL_TEMP)),
            hysteresis=DEFAULT_SAFETY_HYSTERESIS,
            algae=float(c.get(CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD)),
            freeze=float(c.get(CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD)),
            outdoor_medium=float(c.get(CONF_TEMP_OUTDOOR_MEDIUM, DEFAULT_TEMP_OUTDOOR_MEDIUM)),
            daily_hours=self.daily_hours_target,
        )

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Load persisted state, subscribe to sensors and start once HA is up."""
        await self._load_state()
        self.async_set_updated_data(self._build_data())
        self._register_watchers()

        # Device integrations (pump controller, heat pump) must finish their own
        # setup before we command them — wait for HA to be fully started.
        if self.hass.is_running:
            self._start()
        else:
            self._started_unsub = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_ha_started
            )

    async def async_unload(self) -> None:
        if self._ready:
            self._account(dt_util.now())
        await self._save_state()
        if self._started_unsub:
            self._started_unsub()
            self._started_unsub = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._ready = False

    @callback
    def _on_ha_started(self, event: Event) -> None:
        self._started_unsub = None
        self._start()

    @callback
    def _start(self) -> None:
        now = dt_util.now()
        self._ready = True
        self._started_at = now
        self._acct_ts = now
        self._last_save = now
        # Every minute at :05 — accounting, expiry, decision and reconciliation
        self._unsubs.append(async_track_time_change(self.hass, self._async_tick, second=5))
        self.hass.async_create_task(self.async_evaluate_mode())
        _LOGGER.info(
            "Pool Circulation started: season=%s mode=%s automation=%s",
            self.season, self.current_mode, self.automation_enabled,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def _load_state(self) -> None:
        stored = await self._store.async_load() or {}
        today = dt_util.now().date()

        self.automation_enabled = stored.get("automation_enabled", True)
        self.winter_mode = stored.get("winter_mode", False)
        self.current_mode = stored.get("current_mode", MODE_OFF)
        self._safety = stored.get("safety")
        self._last_turned_on = _parse_ts(stored.get("last_turned_on"))
        self._last_turned_off = _parse_ts(stored.get("last_turned_off"))
        self._extra_filter_until = _parse_ts(stored.get("extra_filter_until"))
        self._cover_burst_until = _parse_ts(stored.get("cover_burst_until"))

        if stored.get("date") == today.isoformat():
            # Pre-1.6 storage kept whole hours only
            self._minutes_run = float(stored.get("minutes_run_today", stored.get("hours_run_today", 0) * 60))
            self._minutes_low = float(stored.get("minutes_low_today", stored.get("hours_low_today", 0) * 60))
            self._energy_wh = float(stored.get("energy_wh_today", 0))
            self._cost = float(stored.get("cost_today", 0))
        if stored.get("prices_date") == today.isoformat() and stored.get("prices"):
            self._prices = [INF if p is None else float(p) for p in stored["prices"]]
            self._prices_day = today
        self._day = today

        _LOGGER.debug(
            "Restored state: mode=%s season=%s run=%.0f min low=%.0f min safety=%s "
            "extra_filter_until=%s",
            self.current_mode, self.season, self._minutes_run, self._minutes_low,
            self._safety, self._extra_filter_until,
        )

    async def _save_state(self) -> None:
        self._last_save = dt_util.now()
        await self._store.async_save(
            {
                "date": self._day.isoformat(),
                "minutes_run_today": round(self._minutes_run, 2),
                "minutes_low_today": round(self._minutes_low, 2),
                "energy_wh_today": round(self._energy_wh, 1),
                "cost_today": round(self._cost, 4),
                # Kept for anyone reading the store file / downgrading
                "hours_run_today": int(self._minutes_run // 60),
                "hours_low_today": int(self._minutes_low // 60),
                "current_mode": self.current_mode,
                "automation_enabled": self.automation_enabled,
                "winter_mode": self.winter_mode,
                "safety": self._safety,
                "last_turned_on": _ts(self._last_turned_on),
                "last_turned_off": _ts(self._last_turned_off),
                "extra_filter_until": _ts(self._extra_filter_until),
                "cover_burst_until": _ts(self._cover_burst_until),
                "prices": (
                    [None if p == INF else p for p in self._prices] if self._prices else None
                ),
                "prices_date": self._prices_day.isoformat() if self._prices_day else None,
            }
        )

    # ------------------------------------------------------------------
    # Minute tick
    # ------------------------------------------------------------------
    async def _async_tick(self, now: datetime) -> None:
        now = dt_util.as_local(now)
        self._account(now)
        self._expire_timers(now)
        await self.async_evaluate_mode()
        await self._check_feedback(now)
        if self._last_save is None or now - self._last_save >= timedelta(minutes=SAVE_INTERVAL_MINUTES):
            await self._save_state()

    def _account(self, now: datetime) -> None:
        """Add elapsed time to today's runtime counters."""
        prev, self._acct_ts = self._acct_ts, now
        if now.date() != self._day:
            _LOGGER.info(
                "New day: resetting counters (run %.1f h, low %.1f h, %.2f kWh, cost %.2f yesterday)",
                self._minutes_run / 60, self._minutes_low / 60, self._energy_wh / 1000, self._cost,
            )
            self._day = now.date()
            self._minutes_run = 0.0
            self._minutes_low = 0.0
            self._energy_wh = 0.0
            self._cost = 0.0
            midnight = dt_util.start_of_local_day(now)
            prev = max(prev, midnight) if prev else midnight
            self.hass.async_create_task(self._save_state())
        if prev is None:
            return
        elapsed = (now - prev).total_seconds()
        if elapsed <= 0 or elapsed > MAX_ACCOUNTING_GAP_SECONDS:
            return
        # Energy and cost from the measured power, whatever the switches say
        power = self._power_w()
        if power:
            wh = power * elapsed / 3600
            self._energy_wh += wh
            price = self._state_float(CONF_SENSOR_PRICE)
            if price is not None:
                self._cost += wh / 1000 * price
        if not self._pump_running():
            return
        if self.current_mode == MODE_LOW:
            self._minutes_low += elapsed / 60
        elif self.current_mode in (MODE_MEDIUM, MODE_HIGH):
            self._minutes_run += elapsed / 60

    def _expire_timers(self, now: datetime) -> None:
        if self._extra_filter_until and now >= self._extra_filter_until:
            _LOGGER.info("Extra filter finished")
            self._extra_filter_until = None
            self.hass.bus.async_fire(EVENT_EXTRA_FILTER_CHANGED, {"active": False, "duration_minutes": 0})
            self.hass.async_create_task(self._save_state())
        if self._cover_burst_until and now >= self._cover_burst_until:
            _LOGGER.info("Cover circulation burst finished")
            self._cover_burst_until = None
            self.hass.async_create_task(self._save_state())

    # ------------------------------------------------------------------
    # Watchers
    # ------------------------------------------------------------------
    def _register_watchers(self) -> None:
        cfg = self.cfg
        temps = [e for e in (cfg.get(CONF_SENSOR_POOL_TEMP), cfg.get(CONF_SENSOR_OUTDOOR_TEMP)) if e]
        if temps:
            self._unsubs.append(async_track_state_change_event(self.hass, temps, self._on_temp_changed))

        if cover := cfg.get(CONF_COVER_POOL):
            self._unsubs.append(async_track_state_change_event(self.hass, [cover], self._on_cover_changed))

        devices = [
            e for e in (
                cfg.get(CONF_SWITCH_CIRCULATION), cfg.get(CONF_SWITCH_RPM_LOW),
                cfg.get(CONF_SWITCH_RPM_MEDIUM), cfg.get(CONF_SWITCH_RPM_HIGH),
                cfg.get(CONF_SWITCH_UV_LAMP),
            ) if e
        ]
        if devices:
            self._unsubs.append(async_track_state_change_event(self.hass, devices, self._on_device_changed))

        if feedback := cfg.get(CONF_SENSOR_PUMP_FEEDBACK):
            self._unsubs.append(async_track_state_change_event(self.hass, [feedback], self._on_feedback_changed))

    @callback
    def _on_temp_changed(self, event) -> None:
        """Re-decide as soon as a temperature reading changes.

        Deciding is cheap and only mismatching devices are commanded, so there
        is no need to filter for threshold crossings here.
        """
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or (old_state and new_state.state == old_state.state):
            return
        self.hass.async_create_task(self.async_evaluate_mode())

    @callback
    def _on_device_changed(self, event) -> None:
        """A pump/UV switch came back from unavailable (controller reboot) — re-apply now.

        Ordinary on/off changes are left to the minute tick so a device that
        refuses a command cannot cause a tight command loop.
        """
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state in _UNAVAILABLE:
            return
        if old_state is None or old_state.state in _UNAVAILABLE:
            _LOGGER.info("%s is back (%s) — reconciling", new_state.entity_id, new_state.state)
            self.hass.async_create_task(self.async_evaluate_mode())

    @callback
    def _on_feedback_changed(self, event) -> None:
        """Refresh the cards when the measured speed changes (not on every watt)."""
        actual = self._actual_speed()
        if actual != self._last_actual:
            self._last_actual = actual
            self.async_set_updated_data(self._build_data())

    @callback
    def _on_cover_changed(self, event) -> None:
        """Start a short high-speed burst when the pool cover is opened."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return
        old = old_state.state if old_state else None
        # Ignore unavailable → open (e.g. after a restart): the cover did not move
        if new_state.state == "open" and old not in ("open", None, *_UNAVAILABLE):
            _LOGGER.info("Pool cover opened — %d min high-speed burst", COVER_BURST_MINUTES)
            self._cover_burst_until = dt_util.now() + timedelta(minutes=COVER_BURST_MINUTES)
            self.hass.async_create_task(self._save_state())
            self.hass.async_create_task(self.async_evaluate_mode())

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    async def async_set_extra_filter(self, on: bool) -> None:
        if on:
            duration = self.extra_filter_duration
            self._extra_filter_until = dt_util.now() + timedelta(minutes=duration)
            _LOGGER.info("Extra filter: high speed for %d minutes", duration)
        else:
            self._extra_filter_until = None
        self.hass.bus.async_fire(
            EVENT_EXTRA_FILTER_CHANGED,
            {"active": on, "duration_minutes": self.extra_filter_duration if on else 0},
        )
        await self._save_state()
        await self.async_evaluate_mode()

    async def async_set_winter_mode(self, winter: bool) -> None:
        if winter == self.winter_mode:
            return
        _LOGGER.info("Season changed to %s", SEASON_WINTER if winter else SEASON_SUMMER)
        self.winter_mode = winter
        await self._save_state()
        await self.async_evaluate_mode()

    async def async_set_automation(self, enabled: bool) -> None:
        self.automation_enabled = enabled
        await self._save_state()
        await self.async_evaluate_mode()

    # ------------------------------------------------------------------
    # Decision + application
    # ------------------------------------------------------------------
    def _sensors_pending(self) -> bool:
        """True right after start while configured temperature sensors have no value yet."""
        if not self._started_at:
            return False
        if (dt_util.now() - self._started_at).total_seconds() > _STARTUP_GRACE_SECONDS:
            return False
        return any(
            self.cfg.get(k) and self._state_float(k) is None
            for k in (CONF_SENSOR_POOL_TEMP, CONF_SENSOR_OUTDOOR_TEMP)
        )

    def _today_prices(self, now: datetime) -> list[float] | None:
        """Today's 24 hourly prices — live from the sensor, else the stored copy."""
        today = now.date()
        entity = self.cfg.get(CONF_SENSOR_PRICE)
        state = self.hass.states.get(entity) if entity else None
        if state is not None:
            prices = extract_today_prices(state.attributes, today)
            if prices:
                if prices != self._prices or self._prices_day != today:
                    self._prices, self._prices_day = prices, today
                    self.hass.async_create_task(self._save_state())
                return prices
        if self._prices_day == today and self._prices:
            return self._prices
        if entity and self._price_warned_day != today:
            self._price_warned_day = today
            _LOGGER.warning(
                "No price list for today on %s (%s) — running until today's hours are done",
                entity, describe_attrs(state.attributes) if state else "entity missing",
            )
        return None

    def _inputs(self, now: datetime) -> Inputs:
        return Inputs(
            now=now,
            season=self.season,
            pool_temp=self._state_float(CONF_SENSOR_POOL_TEMP),
            outdoor_temp=self._state_float(CONF_SENSOR_OUTDOOR_TEMP),
            prices=self._today_prices(now),
            current_mode=self.current_mode,
            automation_enabled=self.automation_enabled,
            extra_filter=self.extra_filter_active,
            cover_burst=self.cover_circulation_active,
            in_min_on=self._in_min_on(),
            in_cooldown=self._in_cooldown(),
            hours_run=self._minutes_run / 60,
            hours_low=self._minutes_low / 60,
            prev_safety=self._safety,
        )

    async def async_evaluate_mode(self) -> None:
        """Decide the mode and bring the devices in line with it."""
        if not self._ready:
            self.async_set_updated_data(self._build_data())
            return
        async with self._lock:
            if self._sensors_pending():
                _LOGGER.debug("Waiting for temperature sensors after start")
                return
            now = dt_util.now()
            d = decide(self._inputs(now), self._settings())
            self._decision = d

            if d.safety != self._safety:
                if d.safety:
                    _LOGGER.warning("Safety guard active: %s — %s", d.safety, d.reason)
                else:
                    _LOGGER.info("Safety guard %s released", self._safety)
                self._safety = d.safety
                self.hass.async_create_task(self._save_state())

            if d.mode != self.current_mode:
                self._change_mode(d)
            if d.enforce:
                await self._apply_devices(self.current_mode)
            await self._update_heat_pump(self.current_mode)

        self.async_set_updated_data(self._build_data())

    def _change_mode(self, d: Decision) -> None:
        previous, mode = self.current_mode, d.mode
        _LOGGER.info("Mode %s → %s (%s: %s)", previous, mode, d.rule, d.reason)
        self.current_mode = mode
        now = dt_util.now()
        self._speed_since = now
        if mode == MODE_OFF and previous != MODE_OFF:
            self._last_turned_off = now
        elif mode != MODE_OFF and previous == MODE_OFF:
            self._last_turned_on = now
        self.hass.bus.async_fire(
            EVENT_MODE_CHANGED,
            {
                "previous_mode": previous,
                "mode": mode,
                "season": self.season,
                "rule": d.rule,
                "reason": d.reason,
                "hours_run_today": self.hours_run_today,
                "daily_target": d.target_hours,
            },
        )
        self.hass.async_create_task(self._save_state())

    async def async_set_mode(self, mode: str) -> None:
        """Force a mode on the devices (kept for compatibility with older callers)."""
        self._change_mode(Decision(mode=mode, rule="manual", reason="Set directly"))
        await self._apply_devices(mode)
        self.async_set_updated_data(self._build_data())

    async def _apply_devices(self, mode: str) -> None:
        """Command only the switches that differ from what *mode* needs."""
        cfg = self.cfg
        circ = cfg.get(CONF_SWITCH_CIRCULATION)
        rpm = {
            MODE_LOW: cfg.get(CONF_SWITCH_RPM_LOW),
            MODE_MEDIUM: cfg.get(CONF_SWITCH_RPM_MEDIUM),
            MODE_HIGH: cfg.get(CONF_SWITCH_RPM_HIGH),
        }
        # Stopping: disable the speed controller first, then drop the speed inputs.
        # Running: select the speed first (unwanted speeds off before the wanted
        # one on), then enable the controller.
        if circ and mode == MODE_OFF:
            await self._ensure(circ, False)
        for m, entity in rpm.items():
            if entity and m != mode:
                await self._ensure(entity, False)
        if rpm.get(mode):
            await self._ensure(rpm[mode], True)
        if circ and mode != MODE_OFF:
            await self._ensure(circ, True)
        await self._update_uv_lamp(mode)

    async def _ensure(self, entity_id: str, on: bool) -> bool:
        """Switch *entity_id* to on/off if it is not already. Returns True if commanded."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNAVAILABLE:
            if entity_id not in self._failed_entities:
                self._failed_entities.add(entity_id)
                _LOGGER.warning("%s is unavailable — will retry when it returns", entity_id)
            return False
        if state.state == ("on" if on else "off"):
            self._failed_entities.discard(entity_id)
            return False
        domain = entity_id.split(".", 1)[0]
        try:
            await self.hass.services.async_call(
                domain, "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 — device integrations raise anything
            if entity_id not in self._failed_entities:
                self._failed_entities.add(entity_id)
                _LOGGER.warning("Could not switch %s %s: %s", entity_id, "on" if on else "off", err)
            return False
        self._failed_entities.discard(entity_id)
        _LOGGER.debug("%s → %s", entity_id, "on" if on else "off")
        return True

    # ------------------------------------------------------------------
    # Pump feedback
    # ------------------------------------------------------------------
    async def _check_feedback(self, now: datetime) -> None:
        """Compare what the switches say with what the feedback sensor measures.

        The switches only reflect the controller's outputs. If the pump has not
        followed them after FEEDBACK_GRACE_SECONDS a fault is raised, an event
        fired and the outputs are re-sent every FAULT_RETRY_MINUTES.
        """
        running = self._feedback_running()
        enforced = self._decision is not None and self._decision.enforce
        if running is None or not enforced:
            self._mismatch_since = None
            if self.pump_fault:
                self._set_fault(None, now)
            return

        expected = self.current_mode != MODE_OFF
        fault: str | None = None
        if running != expected:
            fault = "not_running" if expected else "not_stopping"
        elif running and not self._speed_settling(now):
            actual = self._actual_speed()
            if actual not in (None, MODE_OFF, self.current_mode):
                fault = "wrong_speed"

        if fault is None:
            self._mismatch_since = None
            if self.pump_fault:
                self._set_fault(None, now)
                self.async_set_updated_data(self._build_data())
            return

        self._mismatch_since = self._mismatch_since or now
        if (now - self._mismatch_since).total_seconds() < FEEDBACK_GRACE_SECONDS:
            return
        if self.pump_fault != fault:
            self._set_fault(fault, now)
        if self._last_retry is None or now - self._last_retry >= timedelta(minutes=FAULT_RETRY_MINUTES):
            self._last_retry = now
            await self._resend_outputs()
        self.async_set_updated_data(self._build_data())

    def _set_fault(self, fault: str | None, now: datetime) -> None:
        if fault:
            _LOGGER.warning(
                "Pump fault: %s — mode %s but %s reads %s",
                fault, self.current_mode, self.cfg.get(CONF_SENSOR_PUMP_FEEDBACK),
                self._state_float(CONF_SENSOR_PUMP_FEEDBACK),
            )
        else:
            _LOGGER.info("Pump fault %s cleared", self.pump_fault)
            self._last_retry = None
        self.pump_fault = fault
        self._fault_since = now if fault else None
        self.hass.bus.async_fire(
            EVENT_PUMP_FAULT,
            {"fault": fault, "active": fault is not None, "mode": self.current_mode,
             "actual_speed": self._actual_speed(),
             "feedback": self._state_float(CONF_SENSOR_PUMP_FEEDBACK)},
        )

    async def _resend_outputs(self) -> None:
        """Re-send the controller outputs even though the switches already look right."""
        cfg = self.cfg
        mode = self.current_mode
        circ = cfg.get(CONF_SWITCH_CIRCULATION)
        speed = {
            MODE_LOW: cfg.get(CONF_SWITCH_RPM_LOW),
            MODE_MEDIUM: cfg.get(CONF_SWITCH_RPM_MEDIUM),
            MODE_HIGH: cfg.get(CONF_SWITCH_RPM_HIGH),
        }.get(mode)
        _LOGGER.warning("Re-sending pump outputs for mode %s", mode)
        try:
            if mode == MODE_OFF:
                for key in (CONF_SWITCH_CIRCULATION, CONF_SWITCH_RPM_LOW, CONF_SWITCH_RPM_MEDIUM, CONF_SWITCH_RPM_HIGH):
                    if entity := cfg.get(key):
                        await self._call_switch(entity, False)
                return
            # Pulse the enable (or the speed input when there is none): off, pause, on
            target = circ or speed
            if target:
                await self._call_switch(target, False)
                await asyncio.sleep(2)
            if speed:
                await self._call_switch(speed, True)
            if circ:
                await self._call_switch(circ, True)
        except Exception as err:  # noqa: BLE001 — device integrations raise anything
            _LOGGER.warning("Re-sending pump outputs failed: %s", err)

    async def _call_switch(self, entity_id: str, on: bool) -> None:
        await self.hass.services.async_call(
            entity_id.split(".", 1)[0], "turn_on" if on else "turn_off",
            {"entity_id": entity_id}, blocking=True,
        )

    def _speed_bands(self) -> tuple[float, float] | None:
        """(medium from, high from) — from the settings, else by the sensor's unit."""
        cfg = self.cfg
        medium = float(cfg.get(CONF_FEEDBACK_MEDIUM_FROM) or 0)
        high = float(cfg.get(CONF_FEEDBACK_HIGH_FROM) or 0)
        if 0 < medium < high:
            return medium, high
        return AUTO_SPEED_BANDS.get((self._feedback_unit() or "").strip().lower())

    def _actual_speed(self) -> str | None:
        """Speed the pump really runs at, measured by the feedback sensor.

        None without a feedback reading, or when the reading can't be mapped
        to a speed (unknown unit and no limits set) while the pump runs.
        """
        value = self._state_float(CONF_SENSOR_PUMP_FEEDBACK)
        if value is None:
            return None
        threshold = float(self.cfg.get(CONF_FEEDBACK_THRESHOLD, DEFAULT_FEEDBACK_THRESHOLD))
        bands = self._speed_bands()
        if bands is None:
            return MODE_OFF if value <= threshold else None
        return classify_speed(value, threshold, *bands)

    def _speed_settling(self, now: datetime) -> bool:
        return (now - self._speed_since).total_seconds() < SPEED_SETTLE_SECONDS

    def _feedback_running(self) -> bool | None:
        """True/False from the feedback sensor, None when there is none or it has no reading."""
        value = self._state_float(CONF_SENSOR_PUMP_FEEDBACK)
        if value is None:
            return None
        return value > float(self.cfg.get(CONF_FEEDBACK_THRESHOLD, DEFAULT_FEEDBACK_THRESHOLD))

    # ------------------------------------------------------------------
    # Heat pump
    # ------------------------------------------------------------------
    async def _update_heat_pump(self, mode: str) -> None:
        """Summer: keep the heat pump on with a price-dependent target temperature.

        - MEDIUM / HIGH (cheap hours, extra filter, cover burst) → best-price target
        - LOW / OFF → normal target

        Only commands what differs from the heat pump's current state, so the
        minute tick does not hammer cloud APIs.

        Winter: the heat pump is left completely alone — it is typically powered
        down for the season and any call would only time out.
        """
        hp = self.cfg.get(CONF_CLIMATE_HEAT_PUMP)
        if not hp or self.winter_mode:
            return
        state = self.hass.states.get(hp)
        if state is None or state.state in _UNAVAILABLE:
            return

        best = float(self.cfg.get(CONF_HP_TEMP_BEST_PRICE, DEFAULT_HP_TEMP_BEST_PRICE))
        normal = float(self.cfg.get(CONF_HP_TEMP_NORMAL, DEFAULT_HP_TEMP_NORMAL))
        target = best if mode in (MODE_HIGH, MODE_MEDIUM) else normal

        try:
            current = state.attributes.get("temperature")
            if current is None or abs(float(current) - target) > 0.05:
                await self.hass.services.async_call(
                    "climate", "set_temperature",
                    {"entity_id": hp, "temperature": target}, blocking=True,
                )
                _LOGGER.debug("Heat pump target → %.1f°C (mode=%s)", target, mode)
            if state.state == "off":
                await self.hass.services.async_call(
                    "climate", "turn_on", {"entity_id": hp}, blocking=True
                )
                _LOGGER.debug("Heat pump turned on")
            self._failed_entities.discard(hp)
        except Exception as err:  # noqa: BLE001 — third-party climate integrations raise anything
            if hp not in self._failed_entities:
                self._failed_entities.add(hp)
                _LOGGER.warning("Heat pump %s did not respond: %s", hp, err)

    # ------------------------------------------------------------------
    # UV lamp
    # ------------------------------------------------------------------
    async def _update_uv_lamp(self, mode: str) -> None:
        """UV lamp on while the pump runs and the pool cover is not open."""
        uv = self.cfg.get(CONF_SWITCH_UV_LAMP)
        if not uv:
            return
        cover = self.cfg.get(CONF_COVER_POOL)
        cover_state = self.hass.states.get(cover) if cover else None
        cover_open = cover_state is not None and cover_state.state == "open"
        uv_on = mode != MODE_OFF and not cover_open
        if await self._ensure(uv, uv_on):
            self.hass.bus.async_fire(
                EVENT_UV_CHANGED,
                {"uv_on": uv_on, "mode": mode, "cover_open": cover_open},
            )

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------
    def _in_cooldown(self) -> bool:
        return self._cooldown_remaining_seconds() > 0

    def _cooldown_remaining_seconds(self) -> int:
        cooldown = int(self.cfg.get(CONF_COOLDOWN_MINUTES, DEFAULT_COOLDOWN_MINUTES))
        if cooldown == 0 or self._last_turned_off is None:
            return 0
        elapsed = (dt_util.now() - self._last_turned_off).total_seconds()
        return max(0, int(cooldown * 60 - elapsed))

    def _in_min_on(self) -> bool:
        return self._min_on_remaining_seconds() > 0

    def _min_on_remaining_seconds(self) -> int:
        min_on = int(self.cfg.get(CONF_MIN_ON_MINUTES, DEFAULT_MIN_ON_MINUTES))
        if min_on == 0 or self._last_turned_on is None:
            return 0
        elapsed = (dt_util.now() - self._last_turned_on).total_seconds()
        return max(0, int(min_on * 60 - elapsed))

    @staticmethod
    def _remaining_seconds(until: datetime | None) -> int:
        if until is None:
            return 0
        return max(0, int((until - dt_util.now()).total_seconds()))

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def _pump_running(self) -> bool:
        """True when the pump turns — from the feedback sensor if it has a reading."""
        running = self._feedback_running()
        if running is not None:
            return running
        return self._switches_on()

    def _switches_on(self) -> bool:
        """True when the controller is told to run — main switch if there is one, else any speed."""
        if self.cfg.get(CONF_SWITCH_CIRCULATION):
            return self._state_is_on(CONF_SWITCH_CIRCULATION)
        return any(
            self._state_is_on(k) for k in (CONF_SWITCH_RPM_LOW, CONF_SWITCH_RPM_MEDIUM, CONF_SWITCH_RPM_HIGH)
        )

    def _state_is_on(self, conf_key: str) -> bool:
        entity_id = self.cfg.get(conf_key)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == "on"

    def _state_float(self, conf_key: str) -> float | None:
        entity_id = self.cfg.get(conf_key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNAVAILABLE:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _power_key(self) -> str | None:
        """Config key of the sensor that measures power: the power sensor, else the feedback."""
        for key in (CONF_SENSOR_PUMP_POWER, CONF_SENSOR_PUMP_FEEDBACK):
            if self.cfg.get(key) and self._unit(key) in ("W", "kW"):
                return key
        return None

    def _power_w(self) -> float | None:
        """Pump power in W, from whichever sensor measures power (W or kW)."""
        key = self._power_key()
        value = self._state_float(key) if key else None
        if value is None:
            return None
        return max(0.0, value * (1000 if self._unit(key) == "kW" else 1))

    def _rpm(self) -> float | None:
        """Pump rpm from the rpm sensor, else from the feedback sensor if it reports rpm."""
        if self.cfg.get(CONF_SENSOR_PUMP_RPM):
            return self._state_float(CONF_SENSOR_PUMP_RPM)
        if (self._unit(CONF_SENSOR_PUMP_FEEDBACK) or "").lower() == "rpm":
            return self._state_float(CONF_SENSOR_PUMP_FEEDBACK)
        return None

    def _unit(self, conf_key: str) -> str | None:
        entity = self.cfg.get(conf_key)
        state = self.hass.states.get(entity) if entity else None
        unit = state.attributes.get("unit_of_measurement") if state else None
        return unit.strip() if isinstance(unit, str) else None

    def _measures_power(self) -> bool:
        return self._power_key() is not None

    def _price_unit(self) -> str | None:
        entity = self.cfg.get(CONF_SENSOR_PRICE)
        state = self.hass.states.get(entity) if entity else None
        return state.attributes.get("unit_of_measurement") if state else None

    def _feedback_unit(self) -> str | None:
        return self._unit(CONF_SENSOR_PUMP_FEEDBACK)

    def _entities(self) -> dict[str, str | None]:
        """Entity IDs the cards need — own entities via the registry, devices from config."""
        if not self._entity_ids or None in self._entity_ids.values():
            reg = er.async_get(self.hass)
            ids: dict[str, str | None] = {
                key: reg.async_get_entity_id(domain, DOMAIN, f"{self.entry.entry_id}_{suffix}")
                for key, (domain, suffix) in _OWN_ENTITIES.items()
            }
            self._entity_ids = ids
        cfg = self.cfg
        return {
            **self._entity_ids,
            "pool_temp": cfg.get(CONF_SENSOR_POOL_TEMP) or None,
            "outdoor_temp": cfg.get(CONF_SENSOR_OUTDOOR_TEMP) or None,
            "circulation": cfg.get(CONF_SWITCH_CIRCULATION) or None,
            "heat_pump": cfg.get(CONF_CLIMATE_HEAT_PUMP) or None,
            "pump_feedback": cfg.get(CONF_SENSOR_PUMP_FEEDBACK) or None,
            "pump_rpm": cfg.get(CONF_SENSOR_PUMP_RPM) or None,
            "pump_power": cfg.get(CONF_SENSOR_PUMP_POWER) or None,
        }

    # ------------------------------------------------------------------
    # Data snapshot
    # ------------------------------------------------------------------
    def _build_data(self) -> dict:
        now = dt_util.now()
        s = self._settings()
        d = self._decision
        pool = self._state_float(CONF_SENSOR_POOL_TEMP)
        outdoor = self._state_float(CONF_SENSOR_OUTDOOR_TEMP)
        prices = self._prices if self._prices_day == now.date() else None
        target = d.target_hours if d else None
        done = d.done_hours if d else 0.0

        plan = [
            {
                "hour": h,
                "price": None if not prices or prices[h] == INF else round(prices[h], 4),
                "mode": d.plan[h] if d and d.plan else None,
            }
            for h in range(24)
        ]

        return {
            "mode": self.current_mode,
            "season": self.season,
            "winter_mode": self.winter_mode,
            "rule": d.rule if d else ("starting" if not self._ready else None),
            "reason": d.reason if d else "Waiting for Home Assistant to start",
            "safety": d.safety if d else self._safety,
            "winter_tier": d.tier if d else None,
            "automation_enabled": self.automation_enabled,
            "extra_filter_active": self.extra_filter_active,
            "extra_filter_remaining": self._remaining_seconds(self._extra_filter_until),
            "cover_circulation_active": self.cover_circulation_active,
            "hours_run_today": self.hours_run_today,
            "hours_low_today": self.hours_low_today,
            "hours_total_today": round((self._minutes_run + self._minutes_low) / 60, 2),
            "target_hours": target,
            "done_hours": done,
            "hours_remaining": round(max(0.0, (target or 0) - done), 2),
            "must_run": d.must_run if d else False,
            "price": self._state_float(CONF_SENSOR_PRICE),
            "price_unit": self._price_unit(),
            # None when there is no power feedback sensor to measure with
            "energy_today": round(self._energy_wh / 1000, 3) if self._measures_power() else None,
            "cost_today": round(self._cost, 2) if self._measures_power() else None,
            "is_peak_price": is_peak_hour(prices, now.hour),
            "too_cold": pool is not None and pool < s.algae,
            "freeze_risk": bool(d and d.safety),
            "in_cooldown": self._in_cooldown(),
            "cooldown_remaining": self._cooldown_remaining_seconds(),
            "in_min_on": self._in_min_on(),
            "min_on_remaining": self._min_on_remaining_seconds(),
            "outdoor_temp": outdoor,
            "pool_temp": pool,
            "thresholds": {
                "pool_critical": s.pool_critical,
                "algae": s.algae,
                "freeze": s.freeze,
                "outdoor_medium": s.outdoor_medium,
            },
            "pump_running": self._pump_running(),
            "switches_on": self._switches_on(),
            "pump_fault": self.pump_fault,
            "pump_fault_since": self._fault_since.isoformat() if self._fault_since else None,
            "feedback_value": self._state_float(CONF_SENSOR_PUMP_FEEDBACK),
            "feedback_unit": self._feedback_unit(),
            "power": round(p, 1) if (p := self._power_w()) is not None else None,
            "rpm": round(r) if (r := self._rpm()) is not None else None,
            "actual_speed": self._actual_speed(),
            "speed_settling": self.current_mode != MODE_OFF and self._speed_settling(now),
            "uv_on": self._state_is_on(CONF_SWITCH_UV_LAMP),
            "schedule_available": bool(d and d.plan),
            "plan": plan,
            "unavailable_entities": sorted(self._failed_entities),
            "entities": self._entities(),
        }

    async def _async_update_data(self) -> dict:
        return self._build_data()

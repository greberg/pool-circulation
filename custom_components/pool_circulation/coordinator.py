"""Coordinator for Pool Circulation integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BINARY_BEST_PRICE,
    CONF_BINARY_PEAK_PRICE,
    CONF_CLIMATE_HEAT_PUMP,
    CONF_COOLDOWN_MINUTES,
    CONF_MIN_ON_MINUTES,
    CONF_COVER_POOL,
    CONF_DAILY_HOURS,
    CONF_DAILY_LOW_HOURS,
    CONF_EXTRA_FILTER_DURATION,
    CONF_SENSOR_ACTUAL_RPM,
    CONF_SENSOR_OUTDOOR_TEMP,
    CONF_SENSOR_POOL_TEMP,
    CONF_SENSOR_PRICE,
    CONF_SENSOR_PRICE_LEVEL,
    CONF_SWITCH_CIRCULATION,
    CONF_SWITCH_UV_LAMP,
    CONF_RPM_HIGH,
    CONF_RPM_LOW,
    CONF_RPM_MEDIUM,
    CONF_SWITCH_RPM_HIGH,
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    CONF_TEMP_ALGAE_THRESHOLD,
    CONF_TEMP_FREEZE_THRESHOLD,
    CONF_TEMP_OUTDOOR_BUFFER,
    CONF_HP_TEMP_BEST_PRICE,
    CONF_HP_TEMP_NORMAL,
    CONF_POOL_TEMP_HEATING_THRESHOLD,
    COORDINATOR_UPDATE_INTERVAL,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_DAILY_HOURS,
    DEFAULT_DAILY_LOW_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DEFAULT_HP_TEMP_BEST_PRICE,
    DEFAULT_HP_TEMP_NORMAL,
    DEFAULT_POOL_TEMP_HEATING_THRESHOLD,
    DEFAULT_RPM_HIGH,
    DEFAULT_RPM_LOW,
    DEFAULT_RPM_MEDIUM,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
    DEFAULT_TEMP_OUTDOOR_BUFFER,
    DOMAIN,
    EVENT_EXTRA_FILTER_CHANGED,
    EVENT_MODE_CHANGED,
    EVENT_UV_CHANGED,
    MODE_HIGH,
    MODE_LOW,
    MODE_MEDIUM,
    MODE_OFF,
    STORE_KEY,
    STORE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _ts_to_hour(ts: str) -> int:
    """Extract the local hour (0-23) from an ISO-8601 timestamp string."""
    # "2026-05-11T14:00:00+02:00"  or  "2026-05-11 14:00:00"
    try:
        for sep in ("T", " "):
            if sep in ts:
                return int(ts.split(sep)[1][:2])
    except (ValueError, IndexError):
        pass
    return 0


def _parse_price_list(raw: list, source_attr: str, today_str: str | None = None) -> list[float] | None:
    """Try to extract exactly 24 hourly floats from a raw attribute list.

    When *today_str* is supplied (ISO date, e.g. "2026-05-11") dict lists are
    filtered to entries whose timestamp starts with that date before slicing to
    24 entries — this handles multi-day attributes like ``prices`` (48 entries
    spanning today + tomorrow).

    Pads any short result with infinity so missing hours are never scheduled.
    """
    if not raw:
        return None

    first = raw[0]

    # ── plain floats / ints ──────────────────────────────────────────────────
    if isinstance(first, (int, float)):
        try:
            prices = [float(v) for v in raw]
        except (TypeError, ValueError):
            return None
        if len(prices) < 24:
            _LOGGER.debug(
                "Day-ahead: '%s' has %d entries — padding missing hours with ∞",
                source_attr, len(prices),
            )
        return (prices + [float("inf")] * 24)[:24]

    # ── list of dicts ────────────────────────────────────────────────────────
    if isinstance(first, dict):
        _LOGGER.debug(
            "Day-ahead: '%s' is a dict list, keys=%s", source_attr, sorted(first.keys())
        )
        # Supported timestamp keys (for sorting and date-filtering)
        ts_key = next(
            (k for k in ("start_time", "startsAt", "start", "starts_at", "time", "hour_start")
             if k in first),
            None,
        )
        # Supported price keys — ordered by preference
        for price_key in ("price", "total", "price_per_kwh", "energy", "value", "cost", "amount"):
            if price_key not in first:
                continue
            try:
                entries: list = list(raw)
                # Filter to today's date when the list spans multiple days
                if today_str and ts_key:
                    entries = [e for e in entries if str(e.get(ts_key, "")).startswith(today_str)]
                # Sort by timestamp so DST or out-of-order data doesn't scramble hours
                if ts_key:
                    entries = sorted(entries, key=lambda e, _k=ts_key: _ts_to_hour(str(e.get(_k, ""))))
                if not entries:
                    continue
                prices = [float(e[price_key]) for e in entries[:24]]
                if len(prices) < 24:
                    _LOGGER.debug(
                        "Day-ahead: '%s[%s]' has %d entries after filtering — padding",
                        source_attr, price_key, len(prices),
                    )
                return (prices + [float("inf")] * 24)[:24]
            except (KeyError, TypeError, ValueError):
                continue

    return None


def _extract_today_prices(attrs: dict) -> list[float] | None:
    """Extract a 24-float hourly price list from a price sensor's attributes.

    Supports:
    - ``today``     : 24 plain floats (Nordpool HACS, Tibber official)
    - ``prices``    : 48 dicts {start_time, price} — today + tomorrow combined
    - ``data``      : 48 dicts {start_time, price_per_kwh} — today + tomorrow
    - ``raw_today`` : dicts {value} (some Nordpool versions)
    - and several other naming variants

    Multi-day dict lists are date-filtered to today before slicing.
    """
    today_str = datetime.now().date().isoformat()

    # Ordered by reliability: plain 24-float list first, then dict variants
    candidates = [
        ("today",        False),   # 24 plain floats — most reliable, no date filter needed
        ("raw_today",    False),   # Nordpool alternative (dicts with 'value')
        ("prices",       True),    # 48 dicts {start_time, price} — must date-filter
        ("data",         True),    # 48 dicts {start_time, price_per_kwh} — must date-filter
        ("today_prices", False),
        ("prices_today", False),
        ("hourly_prices", False),
        ("current_day",  False),
        ("today_data",   False),
    ]

    for attr, needs_date_filter in candidates:
        raw = attrs.get(attr)
        if not raw or not isinstance(raw, (list, tuple)):
            continue
        result = _parse_price_list(raw, attr, today_str if needs_date_filter else None)
        if result is not None:
            real = sum(1 for p in result if p < float("inf"))
            _LOGGER.debug(
                "Day-ahead schedule built from '%s': %d hours with known prices", attr, real
            )
            return result

    # Nothing found — show actionable debug info
    list_attrs = {k: f"len={len(v)}, sample={v[0]!r}" for k, v in attrs.items() if isinstance(v, (list, tuple)) and v}
    if list_attrs:
        _LOGGER.warning(
            "Day-ahead schedule: no price list found in sensor. "
            "List-type attributes present: %s", list_attrs,
        )
    else:
        _LOGGER.warning(
            "Day-ahead schedule: sensor has no list-type attributes at all. "
            "Scalar attributes: %s",
            {k: v for k, v in attrs.items() if not isinstance(v, (list, tuple, dict))},
        )
    return None


class PoolCirculationCoordinator(DataUpdateCoordinator):
    """Manage pool circulation and heat pump based on electricity price."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=COORDINATOR_UPDATE_INTERVAL),
        )
        self.entry = entry
        self._subscriptions: list[Any] = []
        self._store = Store(hass, STORE_VERSION, f"{STORE_KEY}_{entry.entry_id}")

        self.automation_enabled: bool = True
        self.current_mode: str = MODE_OFF
        self.hours_run_today: int = 0      # MEDIUM + HIGH hours (counts toward daily_hours target)
        self.hours_low_today: int = 0      # LOW-only hours (background trickle)
        self._last_run_hour: int | None = None

        # Extra filter state — not persisted (resets on HA restart)
        self.extra_filter_active: bool = False
        self._extra_filter_task: asyncio.Task | None = None

        # Cover-open circulation burst — 5-min HIGH RPM when cover is opened
        self.cover_circulation_active: bool = False
        self._cover_circulation_task: asyncio.Task | None = None
        self._cover_watcher_unsubs: list[Any] = []

        # Cooldown / min-on: timestamps to prevent rapid on/off cycling
        self._last_turned_off: datetime | None = None
        self._last_turned_on: datetime | None = None

        # Cache of previous options so the smart update listener can diff changes
        self._prev_options: dict = dict(entry.options)

        # Day-ahead schedule — built from full 24-hour price data at midnight.
        # Two tiers: cheapest high_hours → HIGH RPM; next low_hours → LOW RPM.
        # Falls back to reactive binary-sensor mode when price data unavailable.
        self._daily_schedule: set[int] = set()      # HIGH-RPM hours
        self._daily_low_schedule: set[int] = set()  # LOW-RPM hours
        self._schedule_date: str | None = None
        # Track which targets the schedule was built with so a mid-day change
        # to daily_hours or daily_low_hours forces a rebuild.
        self._schedule_high_target: int = -1
        self._schedule_low_target: int = -1

        # Temperature + price binary sensor state-change subscriptions (managed
        # separately so they can be refreshed when options change entity IDs)
        self._temp_watcher_unsubs: list[Any] = []
        self._price_watcher_unsubs: list[Any] = []

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    @property
    def cfg(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def daily_hours_target(self) -> int:
        return int(self.cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS))

    @property
    def daily_low_hours_target(self) -> int:
        return int(self.cfg.get(CONF_DAILY_LOW_HOURS, DEFAULT_DAILY_LOW_HOURS))

    @property
    def extra_filter_duration(self) -> int:
        return int(self.cfg.get(CONF_EXTRA_FILTER_DURATION, DEFAULT_EXTRA_FILTER_DURATION))

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Load persisted state and register hourly + midnight callbacks."""
        await self._load_state()

        # Run at the top of every hour (HH:00:05) to re-evaluate mode
        self._subscriptions.append(
            async_track_time_change(
                self.hass,
                self._hourly_tick,
                minute=0,
                second=5,
            )
        )

        # Reset daily counter at midnight
        self._subscriptions.append(
            async_track_time_change(
                self.hass,
                self._midnight_reset,
                hour=0,
                minute=0,
                second=10,
            )
        )

        # Re-evaluate immediately when temperature or price sensors change state
        # instead of waiting up to 59 minutes for the next hourly tick.
        self._register_temp_watchers()
        self._register_price_watchers()
        self._register_cover_watcher()

        # Defer the initial mode evaluation until HA is fully started so that
        # device integrations (e.g. heat pump) have finished their own setup.
        if self.hass.is_running:
            self.hass.async_create_task(self.async_evaluate_mode())
        else:
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                self._on_ha_started,
            )

        _LOGGER.info("Pool Circulation: hourly price-based scheduler active")

    async def async_unload(self) -> None:
        await self._save_state()  # persist hours_run_today before any reload
        if self._extra_filter_task:
            self._extra_filter_task.cancel()
            self._extra_filter_task = None
        if self._cover_circulation_task:
            self._cover_circulation_task.cancel()
            self._cover_circulation_task = None
        for unsub in self._subscriptions:
            unsub()
        self._subscriptions.clear()
        for unsub in self._temp_watcher_unsubs:
            unsub()
        self._temp_watcher_unsubs.clear()
        for unsub in self._price_watcher_unsubs:
            unsub()
        self._price_watcher_unsubs.clear()
        for unsub in self._cover_watcher_unsubs:
            unsub()
        self._cover_watcher_unsubs.clear()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def _load_state(self) -> None:
        stored = await self._store.async_load() or {}
        today = datetime.now().date().isoformat()
        if stored.get("date") == today:
            self.hours_run_today = stored.get("hours_run_today", 0)
            self.hours_low_today = stored.get("hours_low_today", 0)
            # Restore day-ahead schedule only when it was built for today
            raw_high = stored.get("daily_schedule")
            raw_low = stored.get("daily_low_schedule")
            if raw_high is not None:
                self._daily_schedule = set(raw_high)
                self._daily_low_schedule = set(raw_low or [])
                self._schedule_date = today
                _LOGGER.debug(
                    "Restored day-ahead schedule for %s: HIGH %s LOW %s",
                    today, sorted(self._daily_schedule), sorted(self._daily_low_schedule),
                )
        else:
            self.hours_run_today = 0
            self.hours_low_today = 0
            self._daily_schedule = set()
            self._daily_low_schedule = set()
            self._schedule_date = None
        self.current_mode = stored.get("current_mode", MODE_OFF)
        self.automation_enabled = stored.get("automation_enabled", True)
        for attr, key in (
            ("_last_turned_off", "last_turned_off"),
            ("_last_turned_on", "last_turned_on"),
        ):
            raw_ts = stored.get(key)
            if raw_ts:
                try:
                    setattr(self, attr, datetime.fromisoformat(raw_ts))
                except ValueError:
                    setattr(self, attr, None)

    async def _save_state(self) -> None:
        await self._store.async_save(
            {
                "date": datetime.now().date().isoformat(),
                "hours_run_today": self.hours_run_today,
                "hours_low_today": self.hours_low_today,
                "current_mode": self.current_mode,
                "automation_enabled": self.automation_enabled,
                "last_turned_off": (
                    self._last_turned_off.isoformat() if self._last_turned_off else None
                ),
                "last_turned_on": (
                    self._last_turned_on.isoformat() if self._last_turned_on else None
                ),
                "daily_schedule": sorted(self._daily_schedule),
                "daily_low_schedule": sorted(self._daily_low_schedule),
                "schedule_date": self._schedule_date,
            }
        )

    # ------------------------------------------------------------------
    # Scheduled callbacks
    # ------------------------------------------------------------------
    @callback
    def _on_ha_started(self, event: Event) -> None:
        """HA has fully started — now safe to call device services."""
        self.hass.async_create_task(self.async_evaluate_mode())

    @callback
    def _hourly_tick(self, now: datetime) -> None:
        """Called at HH:00:05 — account for the previous hour then re-evaluate."""
        if self.current_mode == MODE_LOW:
            self.hours_low_today += 1
            _LOGGER.debug("Hourly tick: LOW mode, hours_low_today=%d", self.hours_low_today)
        elif self.current_mode in (MODE_MEDIUM, MODE_HIGH):
            self.hours_run_today += 1
            _LOGGER.debug(
                "Hourly tick: %s mode, hours_run_today=%d", self.current_mode, self.hours_run_today
            )

        # Persist the updated counter immediately so a crash or update between
        # hourly ticks never loses more than the current partial hour.
        # Without this, hours_run_today is only saved on mode changes — if the
        # pump runs in steady HIGH all morning with no mode switch, a restart
        # would reset the counter and could trigger must-run incorrectly.
        self.hass.async_create_task(self._save_state())
        self.hass.async_create_task(self.async_evaluate_mode())

    @callback
    def _midnight_reset(self, now: datetime) -> None:
        """Reset daily hours counter and schedule at midnight."""
        _LOGGER.info(
            "Midnight reset: resetting hours_run_today from %d to 0",
            self.hours_run_today,
        )
        self.hours_run_today = 0
        self.hours_low_today = 0
        # Clear schedule so it gets rebuilt for the new day on the next evaluation.
        # Nordpool refreshes today's prices at midnight so the rebuild will have
        # the correct 24-hour price list for the new day.
        self._daily_schedule = set()
        self._daily_low_schedule = set()
        self._schedule_date = None
        self.hass.async_create_task(self._save_state())
        self.async_set_updated_data(self._build_data())

    # ------------------------------------------------------------------
    # Temperature watchers
    # ------------------------------------------------------------------
    def _register_temp_watchers(self) -> None:
        """Subscribe to pool and outdoor temp sensor state changes.

        Called once during setup and again whenever options change (the sensor
        entity IDs might have been edited). Old subscriptions are replaced.
        """
        for unsub in list(self._temp_watcher_unsubs):
            unsub()
        self._temp_watcher_unsubs.clear()

        for conf_key in (CONF_SENSOR_POOL_TEMP, CONF_SENSOR_OUTDOOR_TEMP):
            entity_id = self.cfg.get(conf_key)
            if entity_id:
                unsub = async_track_state_change_event(
                    self.hass,
                    [entity_id],
                    self._on_temp_changed,
                )
                self._temp_watcher_unsubs.append(unsub)
                _LOGGER.debug("Watching temperature sensor: %s", entity_id)

    def _register_price_watchers(self) -> None:
        """Subscribe to best-price and peak-price binary sensor changes.

        The old automations fired immediately when the price window changed
        (e.g. best-price turned off → switch back to LOW). Without this the
        component would wait up to 55 min for the next hourly tick.
        """
        for unsub in list(self._price_watcher_unsubs):
            unsub()
        self._price_watcher_unsubs.clear()

        for conf_key in (CONF_BINARY_BEST_PRICE, CONF_BINARY_PEAK_PRICE):
            entity_id = self.cfg.get(conf_key)
            if entity_id:
                unsub = async_track_state_change_event(
                    self.hass,
                    [entity_id],
                    self._on_price_changed,
                )
                self._price_watcher_unsubs.append(unsub)
                _LOGGER.debug("Watching price binary sensor: %s", entity_id)

    @callback
    def _on_price_changed(self, event) -> None:
        """Re-evaluate mode immediately when a price binary sensor changes state.

        Handles transitions like best-price window opening/closing or peak-price
        starting/ending without waiting for the next hourly tick.
        Only acts when the value actually changed to on/off (not unavailable).
        """
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return
        if new_state.state not in ("on", "off"):
            return
        if old_state and new_state.state == old_state.state:
            return
        _LOGGER.debug(
            "Price binary sensor %s changed: %s → %s — re-evaluating mode",
            new_state.entity_id,
            old_state.state if old_state else "?",
            new_state.state,
        )
        self.hass.async_create_task(self.async_evaluate_mode())

    @callback
    def _on_temp_changed(self, event) -> None:
        """Re-evaluate mode immediately when a temperature sensor changes.

        Handles both algae skip (pool temp crosses algae threshold) and
        freeze protection (outdoor temp crosses freeze threshold) without
        waiting for the next hourly tick.
        """
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        entity_id = new_state.entity_id

        # Only act when the value actually changes meaningfully — ignore
        # unavailable / unknown transitions that don't cross a threshold.
        try:
            new_val = float(new_state.state)
        except (ValueError, TypeError):
            return

        try:
            old_val = float(old_state.state) if old_state else None
        except (ValueError, TypeError):
            old_val = None

        pool_sensor = self.cfg.get(CONF_SENSOR_POOL_TEMP)
        outdoor_sensor = self.cfg.get(CONF_SENSOR_OUTDOOR_TEMP)

        if entity_id == pool_sensor:
            algae_threshold = float(self.cfg.get(CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD))
            heating_threshold = float(self.cfg.get(CONF_POOL_TEMP_HEATING_THRESHOLD, DEFAULT_POOL_TEMP_HEATING_THRESHOLD))
            crossed_algae = old_val is None or (old_val >= algae_threshold) != (new_val >= algae_threshold)
            crossed_heating = old_val is None or (old_val >= heating_threshold) != (new_val >= heating_threshold)
            if crossed_algae:
                _LOGGER.debug(
                    "Pool temp crossed algae threshold (%.1f°C): %.1f → %.1f — re-evaluating mode",
                    algae_threshold, old_val if old_val is not None else float("nan"), new_val,
                )
            if crossed_heating:
                _LOGGER.debug(
                    "Pool temp crossed heating threshold (%.1f°C): %.1f → %.1f — re-evaluating heat pump",
                    heating_threshold, old_val if old_val is not None else float("nan"), new_val,
                )
            if crossed_algae or crossed_heating:
                self.hass.async_create_task(self.async_evaluate_mode())

        elif entity_id == outdoor_sensor:
            threshold = float(self.cfg.get(CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD))
            if old_val is None or (old_val <= threshold) != (new_val <= threshold):
                _LOGGER.debug(
                    "Outdoor temp crossed freeze threshold (%.1f°C): %.1f → %.1f — re-evaluating mode",
                    threshold, old_val if old_val is not None else float("nan"), new_val,
                )
                self.hass.async_create_task(self.async_evaluate_mode())

    def _register_cover_watcher(self) -> None:
        """Subscribe to pool cover state changes to trigger the opening burst."""
        for unsub in list(self._cover_watcher_unsubs):
            unsub()
        self._cover_watcher_unsubs.clear()

        cover = self.cfg.get(CONF_COVER_POOL)
        if cover:
            unsub = async_track_state_change_event(
                self.hass, [cover], self._on_cover_changed,
            )
            self._cover_watcher_unsubs.append(unsub)
            _LOGGER.debug("Watching pool cover: %s", cover)

    @callback
    def _on_cover_changed(self, event) -> None:
        """Trigger 5-min HIGH circulation burst when the pool cover is opened.

        Circulates the stagnant water that sat under the cover before UV resumes.
        Bypasses cooldown and min-on — the user just opened the cover intentionally.
        """
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        new = new_state.state
        old = old_state.state if old_state else None

        # Only fire when cover transitions TO open (not already open)
        if new == "open" and old != "open":
            _LOGGER.info("Pool cover opened — starting 5-min HIGH circulation burst")
            self.hass.async_create_task(self._start_cover_circulation())

    async def _start_cover_circulation(self) -> None:
        """Activate 5-min HIGH RPM burst after cover opens."""
        # Cancel any running burst (e.g. cover opened twice quickly)
        if self._cover_circulation_task:
            self._cover_circulation_task.cancel()
            self._cover_circulation_task = None

        self.cover_circulation_active = True
        self._cover_circulation_task = self.hass.async_create_task(
            self._cover_circulation_timeout()
        )
        await self.async_evaluate_mode()

    async def _cover_circulation_timeout(self) -> None:
        """Deactivate cover burst after 5 minutes."""
        try:
            await asyncio.sleep(5 * 60)
        except asyncio.CancelledError:
            return
        _LOGGER.info("Cover circulation burst complete — returning to normal schedule")
        self.cover_circulation_active = False
        self._cover_circulation_task = None
        await self.async_evaluate_mode()

    # ------------------------------------------------------------------
    # Extra filter mode
    # ------------------------------------------------------------------
    async def async_set_extra_filter(self, on: bool) -> None:
        """Activate or deactivate extra filter mode."""
        # Cancel any running countdown
        if self._extra_filter_task:
            self._extra_filter_task.cancel()
            self._extra_filter_task = None

        self.extra_filter_active = on

        if on:
            duration = self.extra_filter_duration
            _LOGGER.info(
                "Extra filter activated: running at high RPM for %d minutes", duration
            )
            self._extra_filter_task = self.hass.async_create_task(
                self._extra_filter_timeout(duration)
            )

        self.hass.bus.async_fire(
            EVENT_EXTRA_FILTER_CHANGED,
            {
                "active": on,
                "duration_minutes": self.extra_filter_duration if on else 0,
            },
        )
        await self.async_evaluate_mode()
        await self._save_state()

    async def _extra_filter_timeout(self, minutes: int) -> None:
        """Auto-deactivate extra filter after duration has elapsed."""
        try:
            await asyncio.sleep(minutes * 60)
        except asyncio.CancelledError:
            return
        _LOGGER.info("Extra filter timeout: deactivating after %d minutes", minutes)
        self.extra_filter_active = False
        self._extra_filter_task = None
        self.hass.bus.async_fire(EVENT_EXTRA_FILTER_CHANGED, {"active": False, "duration_minutes": 0})
        await self.async_evaluate_mode()
        await self._save_state()

    # ------------------------------------------------------------------
    # Day-ahead schedule
    # ------------------------------------------------------------------
    def _build_daily_schedule(self) -> tuple[set[int], set[int]]:
        """Pick the cheapest hours from today's full price list into two speed tiers.

        Reads the ``today`` attribute from the configured price sensor — a list of
        24 floats (one per hour) published by Nordpool / Tibber integrations.

        Returns (high_set, low_set):
        - ``high_set``: cheapest ``daily_hours_target`` hours → HIGH RPM
          (thorough filtration + heat pump during cheapest electricity)
        - ``low_set``: next cheapest ``daily_low_hours_target`` hours → LOW RPM
          (light circulation at moderate prices; no overlap with HIGH set)

        Returns (set(), set()) when price data is unavailable; the caller falls
        back to reactive binary-sensor logic in that case.
        """
        high_target = self.daily_hours_target
        low_target = self.daily_low_hours_target

        if high_target <= 0 and low_target <= 0:
            return set(), set()

        price_entity = self.cfg.get(CONF_SENSOR_PRICE)
        if not price_entity:
            return set(), set()

        state = self.hass.states.get(price_entity)
        if not state:
            return set(), set()

        today_prices: list | None = _extract_today_prices(state.attributes)

        if not today_prices or len(today_prices) < 24:
            _LOGGER.warning(
                "Day-ahead schedule: no usable 24-hour price list on %s "
                "(tried 'today' plain floats, 'today' dicts, 'raw_today' dicts). "
                "Available attributes: %s — falling back to reactive binary-sensor mode.",
                price_entity,
                sorted(state.attributes.keys()),
            )
            return set(), set()

        # Rank all 24 hours by price ascending
        ranked = sorted(range(24), key=lambda h: today_prices[h])

        # Tier 1: cheapest high_target hours → HIGH
        high_set = set(ranked[:high_target])

        # Tier 2: next low_target hours (not already in HIGH) → LOW
        remaining = [h for h in ranked if h not in high_set]
        low_set = set(remaining[:low_target])

        def _hours_str(hours: set[int], prices: list) -> str:
            return ", ".join(
                f"{h:02d}h={prices[h]:.4f}" for h in sorted(hours)
            )

        _LOGGER.info(
            "Day-ahead schedule built: HIGH %s | LOW %s",
            _hours_str(high_set, today_prices),
            _hours_str(low_set, today_prices) if low_set else "none",
        )
        return high_set, low_set

    def _maybe_rebuild_schedule(self) -> bool:
        """Rebuild the day-ahead schedule if it is missing or stale.

        Returns True when a valid schedule is available (either already cached or
        just rebuilt). Returns False when price data is not yet available; the
        caller should fall back to reactive binary-sensor mode.

        This is idempotent and cheap when the schedule is already current.
        """
        today = datetime.now().date().isoformat()
        targets_match = (
            self._schedule_high_target == self.daily_hours_target
            and self._schedule_low_target == self.daily_low_hours_target
        )
        # Return cached schedule when it's for today and targets haven't changed
        if (
            self._schedule_date == today
            and (self._daily_schedule or self._daily_low_schedule)
            and targets_match
        ):
            return True

        high_set, low_set = self._build_daily_schedule()
        if high_set or low_set:
            self._daily_schedule = high_set
            self._daily_low_schedule = low_set
            self._schedule_date = today
            self._schedule_high_target = self.daily_hours_target
            self._schedule_low_target = self.daily_low_hours_target
            self.hass.async_create_task(self._save_state())
            return True

        return False  # price data not yet available

    # ------------------------------------------------------------------
    # Mode decision
    # ------------------------------------------------------------------
    def _freeze_risk(self) -> bool:
        """Return True if outdoor temp is at or below the freeze threshold.

        When True the pump must run at low speed to keep water moving and
        prevent the pool and pipes from freezing. Overrides ALL other logic.
        """
        threshold = self.cfg.get(CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD)
        outdoor = self._state_float(CONF_SENSOR_OUTDOOR_TEMP)
        if outdoor is None:
            return False
        if outdoor <= threshold:
            _LOGGER.warning(
                "Freeze protection active: outdoor temp %.1f°C ≤ %.1f°C threshold",
                outdoor,
                threshold,
            )
            return True
        return False

    def _too_cold_to_circulate(self) -> bool:
        """Return True if pool water temp is below the algae growth threshold.

        Pool water temperature decides algae risk. If no pool temp sensor is
        configured the skip is never triggered (safe default: always circulate).
        """
        threshold = self.cfg.get(CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD)
        pool = self._state_float(CONF_SENSOR_POOL_TEMP)

        if pool is None:
            return False

        if pool < threshold:
            _LOGGER.debug(
                "Algae skip active: pool temp %.1f°C < %.1f°C threshold",
                pool,
                threshold,
            )
            return True

        return False

    def _outdoor_buffer_active(self) -> bool:
        """Return True when outdoor temp is in the cold-buffer zone.

        This is the range between the freeze threshold and the algae-stop cutoff
        (default 2°C–4°C). In this zone the pool is cold (below algae threshold)
        but outdoor conditions are borderline — it's safer to keep the pump at
        LOW than to stop it entirely, even though algae skip would normally fire.

        Mirrors the old automation behaviour:
          'Cirkulationspump off pooltemp under 9 and temp over 4'
        which required outdoor > 4°C before stopping the pump on a cold pool.
        """
        outdoor = self._state_float(CONF_SENSOR_OUTDOOR_TEMP)
        if outdoor is None:
            return False
        freeze = float(self.cfg.get(CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD))
        buffer = float(self.cfg.get(CONF_TEMP_OUTDOOR_BUFFER, DEFAULT_TEMP_OUTDOOR_BUFFER))
        # Active when outdoor is above the freeze threshold (freeze protection handles
        # anything colder) but at or below the algae-stop cutoff.
        return freeze < outdoor <= buffer

    def _in_cooldown(self) -> bool:
        """Return True if the pump is within the cooldown window after turning off.

        Freeze protection and extra filter mode bypass cooldown — they are either
        safety-critical or explicitly triggered by the user.
        """
        cooldown = int(self.cfg.get(CONF_COOLDOWN_MINUTES, DEFAULT_COOLDOWN_MINUTES))
        if cooldown == 0 or self._last_turned_off is None:
            return False
        elapsed_seconds = (datetime.now() - self._last_turned_off).total_seconds()
        return elapsed_seconds < cooldown * 60

    def _cooldown_remaining_seconds(self) -> int:
        """Seconds remaining in the current cooldown, or 0 if not in cooldown."""
        cooldown = int(self.cfg.get(CONF_COOLDOWN_MINUTES, DEFAULT_COOLDOWN_MINUTES))
        if cooldown == 0 or self._last_turned_off is None:
            return 0
        elapsed = (datetime.now() - self._last_turned_off).total_seconds()
        remaining = cooldown * 60 - elapsed
        return max(0, int(remaining))

    def _in_min_on(self) -> bool:
        """Return True if the pump is within the minimum on-time window.

        Prevents the pump from being turned off too quickly after starting —
        including by algae skip, peak price, or any other reason. Evaluated
        before algae skip in _decide_mode so a brief temperature dip cannot
        stop the pump within seconds of it turning on.
        Only freeze protection and extra filter bypass this.
        """
        min_on = int(self.cfg.get(CONF_MIN_ON_MINUTES, DEFAULT_MIN_ON_MINUTES))
        if min_on == 0 or self._last_turned_on is None:
            return False
        elapsed_seconds = (datetime.now() - self._last_turned_on).total_seconds()
        return elapsed_seconds < min_on * 60

    def _min_on_remaining_seconds(self) -> int:
        """Seconds remaining in the minimum on-time, or 0 if not constrained."""
        min_on = int(self.cfg.get(CONF_MIN_ON_MINUTES, DEFAULT_MIN_ON_MINUTES))
        if min_on == 0 or self._last_turned_on is None:
            return 0
        elapsed = (datetime.now() - self._last_turned_on).total_seconds()
        remaining = min_on * 60 - elapsed
        return max(0, int(remaining))

    def _scheduling_active(self) -> bool:
        """Return True when price-based scheduling and daily hours target should apply.

        Scheduling is only meaningful when the pool is warm enough for algae to grow —
        i.e. when regular circulation is actually needed. Below the algae threshold the
        water is too cold for biological activity so there is no point running the pump
        for energy/filtration reasons; the cheapest option is simply to stay off.

        If no pool temperature sensor is configured we assume the pool is warm and
        scheduling applies (safe default — better to over-circulate than skip).
        """
        return not self._too_cold_to_circulate()

    def _decide_mode(self) -> str:
        """Determine the target mode from current signals and state.

        Speed mapping:
        - HIGH   : extra filter only (user-triggered) — intensive filtration
        - MEDIUM : scheduled cheap hours (daily_hours target) — normal daily run
        - LOW    : background trickle (daily_low_hours) or freeze protection
        - OFF    : peak price / outside schedule / algae skip

        Priority (highest → lowest):
        1. Freeze protection — outdoor temp ≤ freeze threshold → MEDIUM (bypasses both timers)
        2. Extra filter active → HIGH (bypasses both timers — user explicitly triggered)
        3. Cover opened → HIGH for 5 min (bypass timers — circulate stagnant water)
        4. Automation disabled → hold current mode
        4. Min-on — pump started recently → hold current running mode (blocks algae skip,
           peak price, and any other reason to stop — pump must run its minimum time)
        5. Pool temperature gate — pool below algae threshold:
             outdoor in buffer zone → LOW; otherwise → OFF (no scheduling)
        6. Must-run override — schedule gap cannot cover remaining hours → MEDIUM
        7. Day-ahead schedule — current hour in MEDIUM tier → MEDIUM, LOW tier → LOW
        8. Reactive fallback (no 'today' prices) — best → MEDIUM, normal → MEDIUM/LOW, peak → OFF
        9. Cooldown — pump was recently turned off → temporarily hold OFF
       10. Minimum LOW — pool warm (above algae threshold) → never OFF, always at least LOW
        """
        # 1. Freeze protection overrides everything — safety critical, bypasses timers
        if self._freeze_risk():
            return MODE_MEDIUM

        # 2. Extra filter forces high RPM — user intentionally triggered, bypasses timers
        if self.extra_filter_active:
            return MODE_HIGH

        # 3. Cover-open burst — 5 min HIGH to circulate stagnant water after cover removal
        if self.cover_circulation_active:
            return MODE_HIGH

        # 4. Automation disabled — hold current mode
        if not self.automation_enabled:
            return self.current_mode

        # 4. Min-on — keep pump running if it hasn't met minimum on-time yet.
        #    Evaluated before the temperature gate so a brief dip below threshold
        #    cannot stop the pump within seconds of it starting.
        if self.current_mode != MODE_OFF and self._in_min_on():
            remaining = self._min_on_remaining_seconds()
            _LOGGER.debug(
                "Min-on active: holding pump on for %d more seconds", remaining
            )
            return self.current_mode

        # 5. Pool temperature gate — price and daily hours only apply when the pool
        #    is warm enough for algae growth (above the algae threshold).
        #    Exception: when outdoor temp is in the cold-buffer zone (between freeze
        #    threshold and buffer cutoff, default 2–4°C) keep the pump at LOW even
        #    though the pool is cold — safer than stopping in marginal conditions.
        #    This mirrors the old automation that only stopped the pump when outdoor
        #    temp was above 4°C, so the pump kept running when outdoor was 2–4°C.
        if not self._scheduling_active():
            if self._outdoor_buffer_active():
                _LOGGER.debug(
                    "Pool cold but outdoor in buffer zone (%.1f°C) — holding at LOW",
                    self._state_float(CONF_SENSOR_OUTDOOR_TEMP) or 0.0,
                )
                return MODE_LOW
            _LOGGER.debug(
                "Scheduling inactive: pool too cold (threshold %.1f°C) — pump off",
                float(self.cfg.get(CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD)),
            )
            return MODE_OFF

        # 6. Price logic + must-run (only reached when pool is warm enough)
        now = datetime.now()
        current_hour = now.hour
        hours_left = 24 - current_hour
        hours_needed = max(0, self.daily_hours_target - self.hours_run_today)

        # How many schedule-tier hours remain from this hour onward (inclusive)?
        # When the day-ahead schedule is available we subtract future scheduled
        # hours from must-run's view of "hours needed", because those hours will
        # be delivered by the normal schedule without any override. This prevents
        # a stale/reset hours_run_today from triggering must-run unnecessarily —
        # for example after a component update or HA restart mid-day.
        schedule_built = bool(self._daily_schedule or self._daily_low_schedule)
        future_scheduled = sum(
            1 for h in range(current_hour, 24)
            if h in self._daily_schedule or h in self._daily_low_schedule
        ) if schedule_built else 0

        # Must-run fires only when the schedule cannot cover the remaining gap.
        # Effective_needed = gap that the schedule won't fill on its own.
        effective_needed = max(0, hours_needed - future_scheduled)
        must_run = effective_needed > 0 and effective_needed >= hours_left

        if must_run:
            # During peak-price hours the daily target is sacrificed — running MEDIUM
            # just to hit the counter would cost significantly more than the filtration
            # benefit. The pump still circulates at LOW (minimum-LOW rule, step 10).
            is_peak = self._state_is_on(CONF_BINARY_PEAK_PRICE)
            if is_peak:
                _LOGGER.debug(
                    "Must-run needed (%d gap, %d left) but current hour is peak price — "
                    "holding at LOW, daily target will be partially missed today",
                    effective_needed, hours_left,
                )
                desired = MODE_LOW
            else:
                _LOGGER.debug(
                    "Must-run override: need %d hours, %d scheduled, %d effective gap, "
                    "%d left today",
                    hours_needed, future_scheduled, effective_needed, hours_left,
                )
                desired = MODE_MEDIUM

        elif self._maybe_rebuild_schedule():
            # ── Day-ahead mode ──────────────────────────────────────────────
            # Full 24-hour price list available.
            # Cheapest N hours (daily_hours) → MEDIUM: proper daily circulation.
            # Next M hours (daily_low_hours) → LOW: light background trickle.
            # Everything else → OFF.
            # HIGH is reserved exclusively for the extra-filter switch.
            if current_hour in self._daily_schedule:
                _LOGGER.debug(
                    "Hour %02d is in MEDIUM day-ahead schedule — running MEDIUM",
                    current_hour,
                )
                desired = MODE_MEDIUM
            elif current_hour in self._daily_low_schedule:
                _LOGGER.debug(
                    "Hour %02d is in LOW day-ahead schedule — running LOW", current_hour
                )
                desired = MODE_LOW
            else:
                _LOGGER.debug(
                    "Hour %02d not in day-ahead schedule — off", current_hour
                )
                desired = MODE_OFF

        else:
            # ── Reactive fallback ────────────────────────────────────────────
            # Price sensor does not expose a 'today' attribute (e.g. Tibber,
            # or Nordpool before midnight publication). Fall back to the binary
            # best-price / peak-price sensors.
            # best-price → MEDIUM (daily target hours), normal → LOW if still
            # needed, peak → OFF.
            is_peak = self._state_is_on(CONF_BINARY_PEAK_PRICE)
            is_best = self._state_is_on(CONF_BINARY_BEST_PRICE)
            low_hours_target = self.daily_low_hours_target
            _LOGGER.debug(
                "Reactive mode: is_best=%s is_peak=%s hours_needed=%d",
                is_best, is_peak, hours_needed,
            )
            if is_peak:
                desired = MODE_OFF
            elif is_best:
                desired = MODE_MEDIUM
            elif hours_needed > 0:
                desired = MODE_MEDIUM
            elif low_hours_target > 0:
                # Daily MEDIUM target met but LOW background still desired
                desired = MODE_LOW
            else:
                desired = MODE_OFF

        # 7. Cooldown — don't turn on if pump was recently switched off.
        #    Only applies when going from OFF → ON; ignored when pool forces minimum LOW.
        if desired != MODE_OFF and self.current_mode == MODE_OFF and self._in_cooldown():
            remaining = self._cooldown_remaining_seconds()
            _LOGGER.debug(
                "Cooldown active: holding pump off for %d more seconds", remaining
            )
            desired = MODE_OFF

        # 8. Minimum LOW override — when pool temp is above the algae threshold the
        #    pump must always circulate at least at LOW speed to keep the water clean
        #    and prevent stagnation.  This overrides peak price, cooldown, and every
        #    other reason to stop completely.  Only bypassed by:
        #      • automation disabled (step 3 returns early)
        #      • pool cold / algae skip (step 5 returns early)
        if desired == MODE_OFF and self._scheduling_active():
            _LOGGER.debug(
                "Pool warm: minimum LOW applied — pump never fully off above algae threshold"
            )
            desired = MODE_LOW

        return desired

    # ------------------------------------------------------------------
    # Mode application
    # ------------------------------------------------------------------
    async def async_evaluate_mode(self) -> None:
        """Decide and apply the correct mode. Called hourly and on startup."""
        target = self._decide_mode()
        if target != self.current_mode:
            await self.async_set_mode(target)
        else:
            # Mode unchanged — but heat pump target temp may still need updating
            # (e.g. pool temp crossed the heating threshold without a mode change)
            await self._update_heat_pump(self.current_mode)
            self.async_set_updated_data(self._build_data())

    async def async_set_mode(self, mode: str) -> None:
        """Apply a circulation mode to all physical devices."""
        _LOGGER.info("Mode change: %s → %s", self.current_mode, mode)
        previous = self.current_mode
        self.current_mode = mode

        # Record timestamps for cooldown and min-on enforcement
        if mode == MODE_OFF and previous != MODE_OFF:
            self._last_turned_off = datetime.now()
            _LOGGER.debug("Pump turned off — cooldown starts now")
        elif mode != MODE_OFF and previous == MODE_OFF:
            self._last_turned_on = datetime.now()
            _LOGGER.debug("Pump turned on — min-on timer starts now")

        # --- Circulation pump ---
        circ = self.cfg.get(CONF_SWITCH_CIRCULATION)
        if circ:
            if mode == MODE_OFF:
                await self._switch(circ, False)
            else:
                await self._switch(circ, True)

        # --- RPM switches (mutually exclusive) ---
        rpm_map = {
            MODE_LOW: CONF_SWITCH_RPM_LOW,
            MODE_MEDIUM: CONF_SWITCH_RPM_MEDIUM,
            MODE_HIGH: CONF_SWITCH_RPM_HIGH,
        }
        for m, conf_key in rpm_map.items():
            entity = self.cfg.get(conf_key)
            if entity:
                await self._switch(entity, mode == m)

        # --- Heat pump ---
        await self._update_heat_pump(mode)

        # --- UV lamp ---
        await self._update_uv_lamp(mode)

        self.hass.bus.async_fire(
            EVENT_MODE_CHANGED,
            {
                "previous_mode": previous,
                "mode": mode,
                "hours_run_today": self.hours_run_today,
                "daily_target": self.daily_hours_target,
            },
        )

        self.hass.async_create_task(self._save_state())
        self.async_set_updated_data(self._build_data())

    async def _switch(self, entity_id: str, on: bool) -> None:
        service = "turn_on" if on else "turn_off"
        await self.hass.services.async_call(
            "switch", service, {"entity_id": entity_id}, blocking=True
        )

    # ------------------------------------------------------------------
    # Heat pump
    # ------------------------------------------------------------------
    async def _update_heat_pump(self, mode: str) -> None:
        """Set heat pump target temperature based on current price tier.

        The heat pump is NEVER switched off — it always stays on and manages
        its own temperature control. Only the target temperature changes:

        - HIGH / MEDIUM  (cheap hours / extra filter / cover burst)
                         → best-price target temp (default 31°C)
                           Maximum heating during cheapest electricity.
        - LOW / OFF      (background / off-peak / standby)
                         → normal target temp (default 30°C)
                           Maintains pool temperature without peak consumption.
        """
        hp = self.cfg.get(CONF_CLIMATE_HEAT_PUMP)
        if not hp:
            return

        best_price_temp = float(self.cfg.get(CONF_HP_TEMP_BEST_PRICE, DEFAULT_HP_TEMP_BEST_PRICE))
        normal_temp = float(self.cfg.get(CONF_HP_TEMP_NORMAL, DEFAULT_HP_TEMP_NORMAL))

        target_temp = best_price_temp if mode in (MODE_HIGH, MODE_MEDIUM) else normal_temp

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": hp, "temperature": target_temp},
            blocking=True,
        )
        await self.hass.services.async_call(
            "climate", "turn_on", {"entity_id": hp}, blocking=True
        )
        _LOGGER.debug("Heat pump ON: mode=%s, target=%.1f°C", mode, target_temp)

    # ------------------------------------------------------------------
    # UV lamp
    # ------------------------------------------------------------------
    async def _update_uv_lamp(self, mode: str) -> None:
        """Control UV lamp: on when pump is running and pool cover is not open."""
        uv = self.cfg.get(CONF_SWITCH_UV_LAMP)
        if not uv:
            return

        cover = self.cfg.get(CONF_COVER_POOL)
        cover_open = False
        if cover:
            state = self.hass.states.get(cover)
            cover_open = state is not None and state.state == "open"

        uv_on = mode != MODE_OFF and not cover_open
        previous_uv = self._uv_is_on()

        await self._switch(uv, uv_on)

        if uv_on != previous_uv:
            _LOGGER.debug("UV lamp: %s (mode=%s, cover_open=%s)", "on" if uv_on else "off", mode, cover_open)
            self.hass.bus.async_fire(
                EVENT_UV_CHANGED,
                {
                    "uv_on": uv_on,
                    "mode": mode,
                    "cover_open": cover_open,
                    "active_rpm": self._active_rpm(),
                },
            )

    def _uv_is_on(self) -> bool:
        """Return True if the UV lamp switch is currently on."""
        uv = self.cfg.get(CONF_SWITCH_UV_LAMP)
        if not uv:
            return False
        state = self.hass.states.get(uv)
        return state is not None and state.state == "on"

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def _active_rpm(self) -> int:
        """Return current RPM.

        If an actual RPM sensor is configured, read it directly (e.g. an ESPHome
        sensor that reads the inverter frequency).  Falls back to the switch-derived
        value — returns the configured RPM number for whichever RPM switch is on,
        or 0 when the pump is off.
        """
        # Prefer actual sensor reading
        actual_rpm_entity = self.cfg.get(CONF_SENSOR_ACTUAL_RPM)
        if actual_rpm_entity:
            state = self.hass.states.get(actual_rpm_entity)
            if state and state.state not in ("unavailable", "unknown", ""):
                try:
                    return int(float(state.state))
                except ValueError:
                    pass

        # Fall back to switch-derived RPM
        for switch_key, rpm_key, default in (
            (CONF_SWITCH_RPM_HIGH,   CONF_RPM_HIGH,   DEFAULT_RPM_HIGH),
            (CONF_SWITCH_RPM_MEDIUM, CONF_RPM_MEDIUM, DEFAULT_RPM_MEDIUM),
            (CONF_SWITCH_RPM_LOW,    CONF_RPM_LOW,    DEFAULT_RPM_LOW),
        ):
            entity_id = self.cfg.get(switch_key)
            if entity_id:
                state = self.hass.states.get(entity_id)
                if state and state.state == "on":
                    return int(self.cfg.get(rpm_key, default))
        return 0

    def _hp_state(self) -> str | None:
        """Current HVAC mode of the heat pump climate entity."""
        hp = self.cfg.get(CONF_CLIMATE_HEAT_PUMP)
        if not hp:
            return None
        state = self.hass.states.get(hp)
        return state.state if state else None

    def _hp_attr(self, attr: str):
        """Read an attribute from the heat pump climate entity."""
        hp = self.cfg.get(CONF_CLIMATE_HEAT_PUMP)
        if not hp:
            return None
        state = self.hass.states.get(hp)
        if not state:
            return None
        return state.attributes.get(attr)

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
        if state is None or state.state in ("unavailable", "unknown", ""):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _state_str(self, conf_key: str) -> str | None:
        entity_id = self.cfg.get(conf_key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    # ------------------------------------------------------------------
    # Data snapshot
    # ------------------------------------------------------------------
    def _build_data(self) -> dict:
        now = datetime.now()
        current_hour = now.hour
        hours_left = 24 - current_hour
        hours_needed = max(0, self.daily_hours_target - self.hours_run_today)
        schedule_built = bool(self._daily_schedule or self._daily_low_schedule)
        future_scheduled = sum(
            1 for h in range(current_hour, 24)
            if h in self._daily_schedule or h in self._daily_low_schedule
        ) if schedule_built else 0
        effective_needed = max(0, hours_needed - future_scheduled)
        return {
            "mode": self.current_mode,
            "automation_enabled": self.automation_enabled,
            "extra_filter_active": self.extra_filter_active,
            "cover_circulation_active": self.cover_circulation_active,
            "hours_run_today": self.hours_run_today,
            "hours_low_today": self.hours_low_today,
            "hours_remaining": hours_needed,
            "daily_target": self.daily_hours_target,
            "price": self._state_float(CONF_SENSOR_PRICE),
            "price_level": self._state_str(CONF_SENSOR_PRICE_LEVEL),
            "is_best_price": self._state_is_on(CONF_BINARY_BEST_PRICE),
            "is_peak_price": self._state_is_on(CONF_BINARY_PEAK_PRICE),
            "must_run": effective_needed > 0 and effective_needed >= hours_left,
            "must_run_peak_suppressed": (
                effective_needed > 0 and effective_needed >= hours_left
                and self._state_is_on(CONF_BINARY_PEAK_PRICE)
            ),
            "too_cold": self._too_cold_to_circulate(),
            "outdoor_buffer_active": self._outdoor_buffer_active(),
            "scheduling_active": self._scheduling_active(),
            "freeze_risk": self._freeze_risk(),
            "in_cooldown": self._in_cooldown(),
            "cooldown_remaining": self._cooldown_remaining_seconds(),
            "in_min_on": self._in_min_on(),
            "min_on_remaining": self._min_on_remaining_seconds(),
            "outdoor_temp": self._state_float(CONF_SENSOR_OUTDOOR_TEMP),
            "pool_temp": self._state_float(CONF_SENSOR_POOL_TEMP),
            "active_rpm": self._active_rpm(),
            "uv_on": self._uv_is_on(),
            "hp_mode": self._hp_state(),
            "hp_current_temp": self._hp_attr("current_temperature"),
            "hp_target_temp": self._hp_attr("temperature"),
            "hp_fan_mode": self._hp_attr("fan_mode"),
            "pool_heating_active": (
                self._state_float(CONF_SENSOR_POOL_TEMP) is not None
                and (self._state_float(CONF_SENSOR_POOL_TEMP) or 999)
                < float(self.cfg.get(CONF_POOL_TEMP_HEATING_THRESHOLD, DEFAULT_POOL_TEMP_HEATING_THRESHOLD))
            ),
            # Day-ahead schedule info
            "schedule_available": bool(self._daily_schedule or self._daily_low_schedule),
            "scheduled_high_hours": sorted(self._daily_schedule),
            "scheduled_low_hours": sorted(self._daily_low_schedule),
            # Comma-separated HH:00 string — matches old sensor.pool_pump_schedule format
            "scheduled_medium_str": ",".join(
                f"{h:02d}:00" for h in sorted(self._daily_schedule)
            ),
            "scheduled_low_str": ",".join(
                f"{h:02d}:00" for h in sorted(self._daily_low_schedule)
            ),
        }

    async def _async_update_data(self) -> dict:
        return self._build_data()

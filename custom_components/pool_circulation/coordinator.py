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
    CONF_HP_TEMP_BEST_PRICE,
    CONF_HP_TEMP_NORMAL,
    CONF_POOL_TEMP_HEATING_THRESHOLD,
    COORDINATOR_UPDATE_INTERVAL,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_DAILY_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DEFAULT_HP_TEMP_BEST_PRICE,
    DEFAULT_HP_TEMP_NORMAL,
    DEFAULT_POOL_TEMP_HEATING_THRESHOLD,
    DEFAULT_RPM_HIGH,
    DEFAULT_RPM_LOW,
    DEFAULT_RPM_MEDIUM,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
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
        self.hours_run_today: int = 0
        self._last_run_hour: int | None = None

        # Extra filter state — not persisted (resets on HA restart)
        self.extra_filter_active: bool = False
        self._extra_filter_task: asyncio.Task | None = None

        # Cooldown / min-on: timestamps to prevent rapid on/off cycling
        self._last_turned_off: datetime | None = None
        self._last_turned_on: datetime | None = None

        # Temperature sensor state-change subscriptions (managed separately
        # so they can be refreshed when options change entity IDs)
        self._temp_watcher_unsubs: list[Any] = []

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

        # Re-evaluate immediately when temperature sensors change state.
        # This ensures algae skip and freeze protection react in real time
        # instead of waiting up to 59 minutes for the next hourly tick.
        self._register_temp_watchers()

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
        if self._extra_filter_task:
            self._extra_filter_task.cancel()
            self._extra_filter_task = None
        for unsub in self._subscriptions:
            unsub()
        self._subscriptions.clear()
        for unsub in self._temp_watcher_unsubs:
            unsub()
        self._temp_watcher_unsubs.clear()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def _load_state(self) -> None:
        stored = await self._store.async_load() or {}
        today = datetime.now().date().isoformat()
        if stored.get("date") == today:
            self.hours_run_today = stored.get("hours_run_today", 0)
        else:
            self.hours_run_today = 0
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
                "current_mode": self.current_mode,
                "automation_enabled": self.automation_enabled,
                "last_turned_off": (
                    self._last_turned_off.isoformat() if self._last_turned_off else None
                ),
                "last_turned_on": (
                    self._last_turned_on.isoformat() if self._last_turned_on else None
                ),
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
        if self.current_mode != MODE_OFF:
            self.hours_run_today += 1
            _LOGGER.debug(
                "Hourly tick: pump was on, hours_run_today=%d", self.hours_run_today
            )

        self.hass.async_create_task(self.async_evaluate_mode())

    @callback
    def _midnight_reset(self, now: datetime) -> None:
        """Reset daily hours counter at midnight."""
        _LOGGER.info(
            "Midnight reset: resetting hours_run_today from %d to 0",
            self.hours_run_today,
        )
        self.hours_run_today = 0
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
        # Remove any previously registered temp watchers before re-registering
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

        Prevents the pump from being turned off too quickly after starting.
        Freeze protection can still override the mode (e.g. medium → low) but
        the pump itself stays running, so this constraint is only relevant when
        the desired mode is OFF.
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

    def _decide_mode(self) -> str:
        """Determine the target mode from current signals and state.

        Priority (highest → lowest):
        1. Freeze protection — outdoor temp ≤ freeze threshold → LOW (bypasses both timers)
        2. Extra filter active → HIGH (bypasses both timers — user explicitly triggered)
        3. Automation disabled → hold current mode
        4. Algae skip — pool temp < algae threshold → OFF
        5. Min-on — pump started recently → hold current running mode instead of stopping
        6. Cooldown — pump was recently turned off → hold OFF
        7. Price logic + must-run override
        """
        # 1. Freeze protection overrides everything — safety critical, bypasses timers
        if self._freeze_risk():
            return MODE_LOW

        # 2. Extra filter forces high RPM — user intentionally triggered, bypasses timers
        if self.extra_filter_active:
            return MODE_HIGH

        # 3. Automation disabled — hold current mode
        if not self.automation_enabled:
            return self.current_mode

        # 4. Algae skip — pool water too cold for biological activity
        if self._too_cold_to_circulate():
            return MODE_OFF

        # 5. Price logic + must-run
        is_peak = self._state_is_on(CONF_BINARY_PEAK_PRICE)
        is_best = self._state_is_on(CONF_BINARY_BEST_PRICE)

        now = datetime.now()
        hours_left = 24 - now.hour
        hours_needed = max(0, self.daily_hours_target - self.hours_run_today)
        must_run = hours_needed > 0 and hours_needed >= hours_left

        if must_run:
            _LOGGER.debug(
                "Must-run override: need %d hours, %d left today", hours_needed, hours_left
            )
            desired = MODE_MEDIUM
        elif is_peak:
            desired = MODE_OFF
        elif is_best:
            desired = MODE_HIGH
        elif hours_needed > 0:
            desired = MODE_MEDIUM
        else:
            desired = MODE_OFF

        # 6. Min-on — keep pump running if it hasn't met minimum on-time yet
        if desired == MODE_OFF and self.current_mode != MODE_OFF and self._in_min_on():
            remaining = self._min_on_remaining_seconds()
            _LOGGER.debug(
                "Min-on active: holding pump on for %d more seconds", remaining
            )
            return self.current_mode

        # 7. Cooldown — don't turn on if pump was recently switched off
        if desired != MODE_OFF and self.current_mode == MODE_OFF and self._in_cooldown():
            remaining = self._cooldown_remaining_seconds()
            _LOGGER.debug(
                "Cooldown active: holding pump off for %d more seconds", remaining
            )
            return MODE_OFF

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
        """Set heat pump state and target temperature based on mode and pool temp.

        Rules:
        - MODE_HIGH (best price): turn ON at best-price target temp
        - MODE_MEDIUM / MODE_LOW: turn ON at normal target temp IF pool is below
          the heating threshold (pool is getting cold and needs heating)
        - MODE_OFF: turn OFF (no circulation = no point heating)
        """
        hp = self.cfg.get(CONF_CLIMATE_HEAT_PUMP)
        if not hp:
            return

        best_price_temp = float(self.cfg.get(CONF_HP_TEMP_BEST_PRICE, DEFAULT_HP_TEMP_BEST_PRICE))
        normal_temp = float(self.cfg.get(CONF_HP_TEMP_NORMAL, DEFAULT_HP_TEMP_NORMAL))
        heating_threshold = float(
            self.cfg.get(CONF_POOL_TEMP_HEATING_THRESHOLD, DEFAULT_POOL_TEMP_HEATING_THRESHOLD)
        )

        pool_temp = self._state_float(CONF_SENSOR_POOL_TEMP)
        pool_cold = pool_temp is not None and pool_temp < heating_threshold

        if mode == MODE_HIGH:
            target_temp = best_price_temp
            hp_on = True
        elif mode in (MODE_MEDIUM, MODE_LOW) and pool_cold:
            target_temp = normal_temp
            hp_on = True
        else:
            target_temp = None
            hp_on = False

        if hp_on:
            if target_temp is not None:
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": hp, "temperature": target_temp},
                    blocking=True,
                )
            await self.hass.services.async_call(
                "climate", "turn_on", {"entity_id": hp}, blocking=True
            )
            _LOGGER.debug(
                "Heat pump ON: mode=%s, target=%.1f°C, pool_temp=%s",
                mode, target_temp, pool_temp,
            )
        else:
            await self.hass.services.async_call(
                "climate", "turn_off", {"entity_id": hp}, blocking=True
            )
            _LOGGER.debug("Heat pump OFF: mode=%s", mode)

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
        hours_left = 24 - datetime.now().hour
        hours_needed = max(0, self.daily_hours_target - self.hours_run_today)
        return {
            "mode": self.current_mode,
            "automation_enabled": self.automation_enabled,
            "extra_filter_active": self.extra_filter_active,
            "hours_run_today": self.hours_run_today,
            "hours_remaining": max(0, self.daily_hours_target - self.hours_run_today),
            "daily_target": self.daily_hours_target,
            "price": self._state_float(CONF_SENSOR_PRICE),
            "price_level": self._state_str(CONF_SENSOR_PRICE_LEVEL),
            "is_best_price": self._state_is_on(CONF_BINARY_BEST_PRICE),
            "is_peak_price": self._state_is_on(CONF_BINARY_PEAK_PRICE),
            "must_run": hours_needed > 0 and hours_needed >= hours_left,
            "too_cold": self._too_cold_to_circulate(),
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
        }

    async def _async_update_data(self) -> dict:
        return self._build_data()

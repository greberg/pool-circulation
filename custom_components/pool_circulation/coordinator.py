"""Coordinator for Pool Circulation integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BINARY_BEST_PRICE,
    CONF_BINARY_PEAK_PRICE,
    CONF_CLIMATE_HEAT_PUMP,
    CONF_DAILY_HOURS,
    CONF_SENSOR_PRICE,
    CONF_SENSOR_PRICE_LEVEL,
    CONF_SWITCH_CIRCULATION,
    CONF_SWITCH_RPM_HIGH,
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    COORDINATOR_UPDATE_INTERVAL,
    DEFAULT_DAILY_HOURS,
    DOMAIN,
    EVENT_MODE_CHANGED,
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
        self._last_run_hour: int | None = None  # tracks which hours pump was on

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    @property
    def cfg(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def daily_hours_target(self) -> int:
        return int(self.cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS))

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

        # Defer the initial mode evaluation until HA is fully started so that
        # device integrations (e.g. heat pump) have finished their own setup
        # and won't cancel our service calls.
        if self.hass.is_running:
            # Component was reloaded while HA was already running — safe to act now
            self.hass.async_create_task(self.async_evaluate_mode())
        else:
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                self._on_ha_started,
            )

        _LOGGER.info("Pool Circulation: hourly price-based scheduler active")

    async def async_unload(self) -> None:
        for unsub in self._subscriptions:
            unsub()
        self._subscriptions.clear()

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

    async def _save_state(self) -> None:
        await self._store.async_save(
            {
                "date": datetime.now().date().isoformat(),
                "hours_run_today": self.hours_run_today,
                "current_mode": self.current_mode,
                "automation_enabled": self.automation_enabled,
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
        # Count the hour that just completed if pump was running
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
    # Mode decision
    # ------------------------------------------------------------------
    def _decide_mode(self) -> str:
        """Determine the target mode from current price signals and daily hours."""
        if not self.automation_enabled:
            return self.current_mode

        is_peak = self._state_is_on(CONF_BINARY_PEAK_PRICE)
        is_best = self._state_is_on(CONF_BINARY_BEST_PRICE)

        # Must-run: if we need more hours than we have time left, override price
        now = datetime.now()
        hours_left = 24 - now.hour  # includes the current hour
        hours_needed = max(0, self.daily_hours_target - self.hours_run_today)
        must_run = hours_needed > 0 and hours_needed >= hours_left

        if must_run:
            _LOGGER.debug(
                "Must-run override: need %d hours, %d left today", hours_needed, hours_left
            )
            # Run at medium during must-run (save high RPM for best-price windows)
            return MODE_MEDIUM

        if is_peak:
            return MODE_OFF

        if is_best:
            return MODE_HIGH

        # Normal price: run at medium if we still need hours today
        if hours_needed > 0:
            return MODE_MEDIUM

        return MODE_OFF

    # ------------------------------------------------------------------
    # Mode application
    # ------------------------------------------------------------------
    async def async_evaluate_mode(self) -> None:
        """Decide and apply the correct mode. Called hourly and on startup."""
        target = self._decide_mode()
        if target != self.current_mode:
            await self.async_set_mode(target)
        else:
            self.async_set_updated_data(self._build_data())

    async def async_set_mode(self, mode: str) -> None:
        """Apply a circulation mode to all physical devices."""
        _LOGGER.info("Mode change: %s → %s", self.current_mode, mode)
        previous = self.current_mode
        self.current_mode = mode

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

        # --- Heat pump: only ON during HIGH (best price window) ---
        hp = self.cfg.get(CONF_CLIMATE_HEAT_PUMP)
        if hp:
            if mode == MODE_HIGH:
                await self.hass.services.async_call(
                    "climate", "turn_on", {"entity_id": hp}, blocking=True
                )
            else:
                await self.hass.services.async_call(
                    "climate", "turn_off", {"entity_id": hp}, blocking=True
                )

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
    # State helpers
    # ------------------------------------------------------------------
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
            "hours_run_today": self.hours_run_today,
            "hours_remaining": max(0, self.daily_hours_target - self.hours_run_today),
            "daily_target": self.daily_hours_target,
            "price": self._state_float(CONF_SENSOR_PRICE),
            "price_level": self._state_str(CONF_SENSOR_PRICE_LEVEL),
            "is_best_price": self._state_is_on(CONF_BINARY_BEST_PRICE),
            "is_peak_price": self._state_is_on(CONF_BINARY_PEAK_PRICE),
            "must_run": hours_needed > 0 and hours_needed >= hours_left,
        }

    async def _async_update_data(self) -> dict:
        return self._build_data()

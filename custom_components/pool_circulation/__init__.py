"""Pool Circulation integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BINARY_BEST_PRICE,
    CONF_BINARY_PEAK_PRICE,
    CONF_CLIMATE_HEAT_PUMP,
    CONF_COVER_POOL,
    CONF_SENSOR_ACTUAL_RPM,
    CONF_SENSOR_OUTDOOR_TEMP,
    CONF_SENSOR_POOL_TEMP,
    CONF_SENSOR_PRICE,
    CONF_SENSOR_PRICE_LEVEL,
    CONF_SWITCH_CIRCULATION,
    CONF_SWITCH_RPM_HIGH,
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    CONF_SWITCH_UV_LAMP,
    DOMAIN,
)
from .coordinator import PoolCirculationCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
]

# Options that require a full reload when changed (entity IDs / subscriptions).
# Everything else (daily hours, temps, timers) is handled in-place.
_RELOAD_REQUIRED_OPTIONS = {
    CONF_SENSOR_OUTDOOR_TEMP,
    CONF_SENSOR_POOL_TEMP,
    CONF_SENSOR_ACTUAL_RPM,
    CONF_SWITCH_CIRCULATION,
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    CONF_SWITCH_RPM_HIGH,
    CONF_CLIMATE_HEAT_PUMP,
    CONF_SWITCH_UV_LAMP,
    CONF_COVER_POOL,
    CONF_BINARY_BEST_PRICE,
    CONF_BINARY_PEAK_PRICE,
    CONF_SENSOR_PRICE,
    CONF_SENSOR_PRICE_LEVEL,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    coordinator = PoolCirculationCoordinator(hass, entry)
    await coordinator.async_setup()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_unload()

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update intelligently.

    Only reloads when structural settings change (entity IDs, subscriptions).
    For runtime value changes (daily hours, temperatures, cooldown, min-on)
    the coordinator re-evaluates in-place — sensors stay live and
    hours_run_today is not disrupted.
    """
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]

    prev = coordinator._prev_options
    curr = dict(entry.options)
    changed = {k for k in set(prev) | set(curr) if prev.get(k) != curr.get(k)}
    coordinator._prev_options = curr

    if changed & _RELOAD_REQUIRED_OPTIONS:
        _LOGGER.debug(
            "Pool Circulation: structural options changed %s — reloading",
            changed & _RELOAD_REQUIRED_OPTIONS,
        )
        await async_reload_entry(hass, entry)
    else:
        _LOGGER.debug(
            "Pool Circulation: runtime options changed %s — re-evaluating in-place",
            changed,
        )
        await coordinator.async_evaluate_mode()

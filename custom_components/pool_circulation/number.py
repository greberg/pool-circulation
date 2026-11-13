"""Number platform for Pool Circulation — the settings worth changing from a dashboard.

Everything else lives in the integration's options (Configure).
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DAILY_HOURS,
    CONF_EXTRA_FILTER_DURATION,
    CONF_TEMP_ALGAE_THRESHOLD,
    CONF_TEMP_FREEZE_THRESHOLD,
    DEFAULT_DAILY_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
    DOMAIN,
)
from .coordinator import PoolCirculationCoordinator
from .sensor import device_info


@dataclass(frozen=True)
class _Spec:
    key: str
    default: float
    name: str
    suffix: str
    icon: str
    min: float
    max: float
    step: float
    unit: str


NUMBERS = (
    # Summer: cheapest N hours at medium speed
    _Spec(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS, "Pool Circulation Daily Hours",
          "daily_hours", "mdi:clock", 0, 24, 1, "h"),
    # Pool below this → no algae growth, no filtration hours needed
    _Spec(CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD, "Pool Algae Threshold",
          "algae_threshold", "mdi:bacteria-outline", 2.0, 20.0, 0.5, "°C"),
    # Outdoor at or below this → low speed continuously
    _Spec(CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD, "Pool Frost Threshold",
          "freeze_threshold", "mdi:snowflake-thermometer", -10.0, 8.0, 0.5, "°C"),
    _Spec(CONF_EXTRA_FILTER_DURATION, DEFAULT_EXTRA_FILTER_DURATION, "Pool Extra Filter Duration",
          "extra_filter_duration", "mdi:timer", 5, 480, 5, "min"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(PoolOptionNumber(coordinator, entry, spec) for spec in NUMBERS)


class PoolOptionNumber(CoordinatorEntity, NumberEntity):
    """Number entity backed by a config-entry option."""

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry, spec: _Spec) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._spec = spec
        self._attr_name = spec.name
        self._attr_unique_id = f"{entry.entry_id}_{spec.suffix}"
        self._attr_icon = spec.icon
        self._attr_native_min_value = spec.min
        self._attr_native_max_value = spec.max
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> float:
        return self.coordinator.cfg.get(self._spec.key, self._spec.default)

    async def async_set_native_value(self, value: float) -> None:
        if isinstance(self._spec.step, int):
            value = int(value)
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, self._spec.key: value}
        )

"""Number platform for Pool Circulation — editable runtime targets."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_COOLDOWN_MINUTES,
    CONF_DAILY_HOURS,
    CONF_EXTRA_FILTER_DURATION,
    CONF_HP_TEMP_BEST_PRICE,
    CONF_HP_TEMP_NORMAL,
    CONF_MIN_ON_MINUTES,
    CONF_POOL_TEMP_HEATING_THRESHOLD,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_DAILY_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DEFAULT_HP_TEMP_BEST_PRICE,
    DEFAULT_HP_TEMP_NORMAL,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_POOL_TEMP_HEATING_THRESHOLD,
    DOMAIN,
)
from .coordinator import PoolCirculationCoordinator

_DEVICE_INFO = lambda entry: {
    "identifiers": {(DOMAIN, entry.entry_id)},
    "name": entry.title,
    "manufacturer": "Pool Circulation",
    "model": "Price-based Scheduler",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PoolDailyHoursNumber(coordinator, entry),
            PoolExtraFilterDurationNumber(coordinator, entry),
            PoolPumpCooldownNumber(coordinator, entry),
            PoolPumpMinOnNumber(coordinator, entry),
            PoolHpTempBestPriceNumber(coordinator, entry),
            PoolHpTempNormalNumber(coordinator, entry),
            PoolPoolHeatingThresholdNumber(coordinator, entry),
        ]
    )


class PoolDailyHoursNumber(CoordinatorEntity, NumberEntity):
    """Editable target for daily circulation hours."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Circulation Daily Hours"
        self._attr_unique_id = f"{entry.entry_id}_daily_hours"
        self._attr_icon = "mdi:clock"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 24
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "h"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_DAILY_HOURS: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_request_refresh()


class PoolExtraFilterDurationNumber(CoordinatorEntity, NumberEntity):
    """How long extra filter mode runs before auto-disabling (minutes)."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Extra Filter Duration"
        self._attr_unique_id = f"{entry.entry_id}_extra_filter_duration"
        self._attr_icon = "mdi:timer"
        self._attr_native_min_value = 5
        self._attr_native_max_value = 480
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = "min"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_EXTRA_FILTER_DURATION, DEFAULT_EXTRA_FILTER_DURATION)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_EXTRA_FILTER_DURATION: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_request_refresh()


class PoolPumpCooldownNumber(CoordinatorEntity, NumberEntity):
    """Minimum minutes to wait before turning the pump back on after it turned off.

    Prevents rapid on/off cycling caused by price signal edge cases or temperature
    fluctuations near a threshold. Set to 0 to disable cooldown entirely.
    Freeze protection and extra filter mode always bypass the cooldown.
    """

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Pump Cooldown"
        self._attr_unique_id = f"{entry.entry_id}_cooldown_minutes"
        self._attr_icon = "mdi:timer-pause"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 60
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "min"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_COOLDOWN_MINUTES, DEFAULT_COOLDOWN_MINUTES)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_COOLDOWN_MINUTES: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_request_refresh()


class PoolPumpMinOnNumber(CoordinatorEntity, NumberEntity):
    """Minimum minutes the pump must stay on once started.

    Prevents the pump from stopping too quickly after turning on due to
    price signal changes or temperature fluctuations near a threshold.
    Set to 0 to disable. Freeze protection and extra filter mode bypass this.
    """

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Pump Minimum On Time"
        self._attr_unique_id = f"{entry.entry_id}_min_on_minutes"
        self._attr_icon = "mdi:timer-play"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 60
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "min"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_MIN_ON_MINUTES, DEFAULT_MIN_ON_MINUTES)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_MIN_ON_MINUTES: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_request_refresh()


class PoolHpTempBestPriceNumber(CoordinatorEntity, NumberEntity):
    """Heat pump target temperature during best-price hours."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Heat Pump Target (Best Price)"
        self._attr_unique_id = f"{entry.entry_id}_hp_temp_best_price"
        self._attr_icon = "mdi:thermometer-chevron-up"
        self._attr_native_min_value = 15.0
        self._attr_native_max_value = 40.0
        self._attr_native_step = 0.5
        self._attr_native_unit_of_measurement = "°C"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_HP_TEMP_BEST_PRICE, DEFAULT_HP_TEMP_BEST_PRICE)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_HP_TEMP_BEST_PRICE: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_evaluate_mode()


class PoolHpTempNormalNumber(CoordinatorEntity, NumberEntity):
    """Heat pump target temperature during normal (non-best-price) hours."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Heat Pump Target (Normal)"
        self._attr_unique_id = f"{entry.entry_id}_hp_temp_normal"
        self._attr_icon = "mdi:thermometer"
        self._attr_native_min_value = 15.0
        self._attr_native_max_value = 40.0
        self._attr_native_step = 0.5
        self._attr_native_unit_of_measurement = "°C"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_HP_TEMP_NORMAL, DEFAULT_HP_TEMP_NORMAL)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_HP_TEMP_NORMAL: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_evaluate_mode()


class PoolPoolHeatingThresholdNumber(CoordinatorEntity, NumberEntity):
    """Pool temperature below which the heat pump turns on during any running mode."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Heating Threshold"
        self._attr_unique_id = f"{entry.entry_id}_pool_heating_threshold"
        self._attr_icon = "mdi:thermometer-low"
        self._attr_native_min_value = 10.0
        self._attr_native_max_value = 35.0
        self._attr_native_step = 0.5
        self._attr_native_unit_of_measurement = "°C"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_POOL_TEMP_HEATING_THRESHOLD, DEFAULT_POOL_TEMP_HEATING_THRESHOLD)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_POOL_TEMP_HEATING_THRESHOLD: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self.coordinator.async_evaluate_mode()

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
    CONF_DAILY_LOW_HOURS,
    CONF_EXTRA_FILTER_DURATION,
    CONF_HP_TEMP_BEST_PRICE,
    CONF_HP_TEMP_NORMAL,
    CONF_MIN_ON_MINUTES,
    CONF_POOL_CRITICAL_TEMP,
    CONF_POOL_TEMP_HEATING_THRESHOLD,
    CONF_TEMP_ALGAE_THRESHOLD,
    CONF_TEMP_FREEZE_THRESHOLD,
    CONF_TEMP_OUTDOOR_BUFFER,
    CONF_WINTER_ALGAE_HOURS,
    CONF_WINTER_MIN_HOURS,
    CONF_WINTER_SKIP_HOURS,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_DAILY_HOURS,
    DEFAULT_DAILY_LOW_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DEFAULT_HP_TEMP_BEST_PRICE,
    DEFAULT_HP_TEMP_NORMAL,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_POOL_CRITICAL_TEMP,
    DEFAULT_POOL_TEMP_HEATING_THRESHOLD,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
    DEFAULT_TEMP_OUTDOOR_BUFFER,
    DEFAULT_WINTER_ALGAE_HOURS,
    DEFAULT_WINTER_MIN_HOURS,
    DEFAULT_WINTER_SKIP_HOURS,
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
            PoolDailyLowHoursNumber(coordinator, entry),
            PoolExtraFilterDurationNumber(coordinator, entry),
            PoolPumpCooldownNumber(coordinator, entry),
            PoolPumpMinOnNumber(coordinator, entry),
            PoolHpTempBestPriceNumber(coordinator, entry),
            PoolHpTempNormalNumber(coordinator, entry),
            PoolPoolHeatingThresholdNumber(coordinator, entry),
            PoolOutdoorBufferNumber(coordinator, entry),
            *(_OptionNumber(coordinator, entry, spec) for spec in _OPTION_NUMBERS),
        ]
    )


class PoolDailyHoursNumber(CoordinatorEntity, NumberEntity):
    """Target hours per day to run the pump at MEDIUM RPM.

    The day-ahead scheduler selects the cheapest N hours from the 24-hour
    price list and runs the pump at MEDIUM speed during those hours —
    thorough filtration + heat pump at best-price target temperature.
    HIGH speed is reserved for the extra-filter switch only.
    """

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


class PoolDailyLowHoursNumber(CoordinatorEntity, NumberEntity):
    """Additional LOW-RPM hours to schedule per day.

    After the MEDIUM-RPM hours are filled with the cheapest electricity, the
    next cheapest ``low_hours`` are scheduled at LOW RPM — light background
    circulation outside the main filtration window. Keeps water moving at
    minimal energy cost. Set to 0 to disable LOW-tier scheduling entirely.
    These hours are always separate from (and never overlap) the daily MEDIUM hours.
    """

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Circulation Daily Low Hours"
        self._attr_unique_id = f"{entry.entry_id}_daily_low_hours"
        self._attr_icon = "mdi:clock-minus"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 24
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "h"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_DAILY_LOW_HOURS, DEFAULT_DAILY_LOW_HOURS)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_DAILY_LOW_HOURS: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)


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


class PoolOutdoorBufferNumber(CoordinatorEntity, NumberEntity):
    """Outdoor temperature below which algae-skip is suppressed (cold-buffer zone).

    When outdoor temp is between the freeze threshold and this value the pump keeps
    running at LOW even if pool water is below the algae threshold — safer than
    stopping in marginal conditions. Matches the old automation behaviour that only
    stopped the pump when outdoor temp was above 4°C.
    Set equal to the freeze threshold to disable the buffer entirely.
    """

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Outdoor Buffer Temperature"
        self._attr_unique_id = f"{entry.entry_id}_outdoor_buffer_temp"
        self._attr_icon = "mdi:thermometer-alert"
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 10.0
        self._attr_native_step = 0.5
        self._attr_native_unit_of_measurement = "°C"
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(CONF_TEMP_OUTDOOR_BUFFER, DEFAULT_TEMP_OUTDOOR_BUFFER)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, CONF_TEMP_OUTDOOR_BUFFER: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)


# ---------------------------------------------------------------------------
# Season / safety settings — same behaviour as the classes above, table-driven
# (key, default, name, unique_id suffix, icon, min, max, step, unit)
# ---------------------------------------------------------------------------
_OPTION_NUMBERS = (
    # Pool ≤ this → MEDIUM continuously in any season. Approaching 3 °C the
    # circulation can fail, so keep a margin above it.
    (CONF_POOL_CRITICAL_TEMP, DEFAULT_POOL_CRITICAL_TEMP, "Pool Critical Temperature",
     "pool_critical_temp", "mdi:snowflake-alert", 1.0, 10.0, 0.5, "°C"),
    # Pool below this → no algae growth, no filtration hours needed
    (CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD, "Pool Algae Threshold",
     "algae_threshold", "mdi:bacteria-outline", 2.0, 20.0, 0.5, "°C"),
    # Outdoor ≤ this → LOW continuously (frost guard)
    (CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD, "Pool Frost Threshold",
     "freeze_threshold", "mdi:snowflake-thermometer", -10.0, 8.0, 0.5, "°C"),
    # Winter, outdoor in buffer zone: how many of the priciest hours may be skipped
    (CONF_WINTER_SKIP_HOURS, DEFAULT_WINTER_SKIP_HOURS, "Pool Winter Skip Hours",
     "winter_skip_hours", "mdi:cash-remove", 0, 12, 1, "h"),
    # Winter, pool ≥ algae threshold: cheapest hours at LOW per day
    (CONF_WINTER_ALGAE_HOURS, DEFAULT_WINTER_ALGAE_HOURS, "Pool Winter Algae Hours",
     "winter_algae_hours", "mdi:clock-plus-outline", 0, 24, 1, "h"),
    # Winter, pool cold and outdoor mild: cheapest hours at LOW per day
    (CONF_WINTER_MIN_HOURS, DEFAULT_WINTER_MIN_HOURS, "Pool Winter Minimum Hours",
     "winter_min_hours", "mdi:clock-outline", 0, 24, 1, "h"),
)


class _OptionNumber(CoordinatorEntity, NumberEntity):
    """Number entity backed by a config-entry option."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry, spec: tuple) -> None:
        super().__init__(coordinator)
        key, default, name, suffix, icon, lo, hi, step, unit = spec
        self._entry = entry
        self._key = key
        self._default = default
        self._is_int = isinstance(step, int)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_icon = icon
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def native_value(self) -> float:
        cfg = {**self._entry.data, **self._entry.options}
        return cfg.get(self._key, self._default)

    async def async_set_native_value(self, value: float) -> None:
        new_options = {**self._entry.options, self._key: int(value) if self._is_int else value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

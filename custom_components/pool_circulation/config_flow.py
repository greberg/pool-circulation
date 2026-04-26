"""Config flow for Pool Circulation integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BINARY_BEST_PRICE,
    CONF_BINARY_PEAK_PRICE,
    CONF_CLIMATE_HEAT_PUMP,
    CONF_COOLDOWN_MINUTES,
    CONF_MIN_ON_MINUTES,
    CONF_COVER_POOL,
    CONF_DAILY_HOURS,
    CONF_EXTRA_FILTER_DURATION,
    CONF_RPM_HIGH,
    CONF_RPM_LOW,
    CONF_RPM_MEDIUM,
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
    CONF_TEMP_ALGAE_THRESHOLD,
    CONF_TEMP_FREEZE_THRESHOLD,
    CONF_HP_TEMP_BEST_PRICE,
    CONF_HP_TEMP_NORMAL,
    CONF_POOL_TEMP_HEATING_THRESHOLD,
    DEFAULT_BINARY_BEST_PRICE,
    DEFAULT_BINARY_PEAK_PRICE,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_DAILY_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DEFAULT_NAME,
    DEFAULT_RPM_HIGH,
    DEFAULT_RPM_LOW,
    DEFAULT_RPM_MEDIUM,
    DEFAULT_SENSOR_PRICE,
    DEFAULT_SENSOR_PRICE_LEVEL,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
    DEFAULT_HP_TEMP_BEST_PRICE,
    DEFAULT_HP_TEMP_NORMAL,
    DEFAULT_POOL_TEMP_HEATING_THRESHOLD,
    DOMAIN,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default=DEFAULT_NAME): str,
        # Physical devices
        vol.Required(CONF_CLIMATE_HEAT_PUMP): str,
        vol.Required(CONF_SWITCH_CIRCULATION): str,
        vol.Optional(CONF_SWITCH_RPM_LOW, default=""): str,
        vol.Optional(CONF_RPM_LOW, default=DEFAULT_RPM_LOW): vol.Coerce(int),
        vol.Optional(CONF_SWITCH_RPM_MEDIUM, default=""): str,
        vol.Optional(CONF_RPM_MEDIUM, default=DEFAULT_RPM_MEDIUM): vol.Coerce(int),
        vol.Optional(CONF_SWITCH_RPM_HIGH, default=""): str,
        vol.Optional(CONF_RPM_HIGH, default=DEFAULT_RPM_HIGH): vol.Coerce(int),
        # Price signals
        vol.Optional(CONF_SENSOR_PRICE, default=DEFAULT_SENSOR_PRICE): str,
        vol.Optional(CONF_SENSOR_PRICE_LEVEL, default=DEFAULT_SENSOR_PRICE_LEVEL): str,
        vol.Optional(CONF_BINARY_BEST_PRICE, default=DEFAULT_BINARY_BEST_PRICE): str,
        vol.Optional(CONF_BINARY_PEAK_PRICE, default=DEFAULT_BINARY_PEAK_PRICE): str,
        # Schedule
        vol.Required(CONF_DAILY_HOURS, default=DEFAULT_DAILY_HOURS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=24)
        ),
        # Temperature sensors for cold-weather override
        vol.Optional(CONF_SENSOR_OUTDOOR_TEMP, default=""): str,
        vol.Optional(CONF_SENSOR_POOL_TEMP, default=""): str,
        vol.Optional(
            CONF_TEMP_ALGAE_THRESHOLD, default=DEFAULT_TEMP_ALGAE_THRESHOLD
        ): vol.Coerce(float),
        vol.Optional(
            CONF_TEMP_FREEZE_THRESHOLD, default=DEFAULT_TEMP_FREEZE_THRESHOLD
        ): vol.Coerce(float),
        # Heat pump temperature targets
        vol.Optional(
            CONF_HP_TEMP_BEST_PRICE, default=DEFAULT_HP_TEMP_BEST_PRICE
        ): vol.Coerce(float),
        vol.Optional(
            CONF_HP_TEMP_NORMAL, default=DEFAULT_HP_TEMP_NORMAL
        ): vol.Coerce(float),
        vol.Optional(
            CONF_POOL_TEMP_HEATING_THRESHOLD, default=DEFAULT_POOL_TEMP_HEATING_THRESHOLD
        ): vol.Coerce(float),
        # Actual RPM sensor (optional — overrides switch-derived RPM)
        vol.Optional(CONF_SENSOR_ACTUAL_RPM, default=""): str,
        # UV lamp and pool cover
        vol.Optional(CONF_SWITCH_UV_LAMP, default=""): str,
        vol.Optional(CONF_COVER_POOL, default=""): str,
        # Extra filter duration (minutes)
        vol.Optional(
            CONF_EXTRA_FILTER_DURATION, default=DEFAULT_EXTRA_FILTER_DURATION
        ): vol.All(vol.Coerce(int), vol.Range(min=5, max=480)),
        # Cooldown between pump off → on (0 = disabled)
        vol.Optional(
            CONF_COOLDOWN_MINUTES, default=DEFAULT_COOLDOWN_MINUTES
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
        # Minimum on-time before pump can turn off (0 = disabled)
        vol.Optional(
            CONF_MIN_ON_MINUTES, default=DEFAULT_MIN_ON_MINUTES
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
    }
)


class PoolCirculationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Circulation."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            return self.async_create_entry(
                title=user_input.get("name", DEFAULT_NAME),
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return PoolCirculationOptionsFlow(config_entry)


class PoolCirculationOptionsFlow(config_entries.OptionsFlow):
    """Pool Circulation options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        cfg = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DAILY_HOURS,
                    default=cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=24)),
                vol.Optional(
                    CONF_BINARY_BEST_PRICE,
                    default=cfg.get(CONF_BINARY_BEST_PRICE, DEFAULT_BINARY_BEST_PRICE),
                ): str,
                vol.Optional(
                    CONF_BINARY_PEAK_PRICE,
                    default=cfg.get(CONF_BINARY_PEAK_PRICE, DEFAULT_BINARY_PEAK_PRICE),
                ): str,
                vol.Optional(
                    CONF_SENSOR_PRICE,
                    default=cfg.get(CONF_SENSOR_PRICE, DEFAULT_SENSOR_PRICE),
                ): str,
                vol.Optional(
                    CONF_SENSOR_OUTDOOR_TEMP,
                    default=cfg.get(CONF_SENSOR_OUTDOOR_TEMP, ""),
                ): str,
                vol.Optional(
                    CONF_SENSOR_POOL_TEMP,
                    default=cfg.get(CONF_SENSOR_POOL_TEMP, ""),
                ): str,
                vol.Optional(
                    CONF_TEMP_ALGAE_THRESHOLD,
                    default=cfg.get(CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_TEMP_FREEZE_THRESHOLD,
                    default=cfg.get(CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HP_TEMP_BEST_PRICE,
                    default=cfg.get(CONF_HP_TEMP_BEST_PRICE, DEFAULT_HP_TEMP_BEST_PRICE),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HP_TEMP_NORMAL,
                    default=cfg.get(CONF_HP_TEMP_NORMAL, DEFAULT_HP_TEMP_NORMAL),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_POOL_TEMP_HEATING_THRESHOLD,
                    default=cfg.get(CONF_POOL_TEMP_HEATING_THRESHOLD, DEFAULT_POOL_TEMP_HEATING_THRESHOLD),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_SENSOR_ACTUAL_RPM,
                    default=cfg.get(CONF_SENSOR_ACTUAL_RPM, ""),
                ): str,
                vol.Optional(
                    CONF_SWITCH_UV_LAMP,
                    default=cfg.get(CONF_SWITCH_UV_LAMP, ""),
                ): str,
                vol.Optional(
                    CONF_COVER_POOL,
                    default=cfg.get(CONF_COVER_POOL, ""),
                ): str,
                vol.Optional(
                    CONF_EXTRA_FILTER_DURATION,
                    default=cfg.get(CONF_EXTRA_FILTER_DURATION, DEFAULT_EXTRA_FILTER_DURATION),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=480)),
                vol.Optional(
                    CONF_COOLDOWN_MINUTES,
                    default=cfg.get(CONF_COOLDOWN_MINUTES, DEFAULT_COOLDOWN_MINUTES),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
                vol.Optional(
                    CONF_MIN_ON_MINUTES,
                    default=cfg.get(CONF_MIN_ON_MINUTES, DEFAULT_MIN_ON_MINUTES),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)

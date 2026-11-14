"""Config flow for Pool Circulation.

One form, the same for setup and Configure:
- the six entities the integration needs (three speed switches, price,
  pool and outdoor temperature) picked from existing entities
- daily hours and the algae / frost thresholds
- a collapsed "Optional devices" section (pump on/off, heat pump, UV, cover)
- a collapsed "Advanced" section for everything else
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

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
    CONF_SENSOR_PRICE,
    CONF_SENSOR_PUMP_FEEDBACK,
    CONF_SENSOR_PUMP_POWER,
    CONF_SENSOR_PUMP_RPM,
    CONF_SWITCH_CIRCULATION,
    CONF_SWITCH_RPM_HIGH,
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    CONF_SWITCH_UV_LAMP,
    CONF_TEMP_ALGAE_THRESHOLD,
    CONF_TEMP_FREEZE_THRESHOLD,
    CONF_TEMP_OUTDOOR_MEDIUM,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_DAILY_HOURS,
    DEFAULT_EXTRA_FILTER_DURATION,
    DEFAULT_FEEDBACK_SPEED_FROM,
    DEFAULT_FEEDBACK_THRESHOLD,
    DEFAULT_HP_TEMP_BEST_PRICE,
    DEFAULT_HP_TEMP_NORMAL,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_NAME,
    DEFAULT_POOL_CRITICAL_TEMP,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
    DEFAULT_TEMP_OUTDOOR_MEDIUM,
    DOMAIN,
    OPTIONAL_ENTITIES,
)

SECTION_OPTIONAL = "optional_devices"
SECTION_ADVANCED = "advanced"

_SWITCH = EntitySelector(EntitySelectorConfig(domain=["switch", "input_boolean"]))
_TEMP = EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))
_PRICE = EntitySelector(EntitySelectorConfig(domain="sensor"))

# key → (default, min, max, step, unit)
_MAIN_NUMBERS = {
    CONF_DAILY_HOURS: (DEFAULT_DAILY_HOURS, 0, 24, 1, "h"),
    CONF_TEMP_ALGAE_THRESHOLD: (DEFAULT_TEMP_ALGAE_THRESHOLD, 2, 20, 0.5, "°C"),
    CONF_TEMP_FREEZE_THRESHOLD: (DEFAULT_TEMP_FREEZE_THRESHOLD, -10, 8, 0.5, "°C"),
}
_ADVANCED_NUMBERS = {
    CONF_POOL_CRITICAL_TEMP: (DEFAULT_POOL_CRITICAL_TEMP, 1, 10, 0.5, "°C"),
    CONF_TEMP_OUTDOOR_MEDIUM: (DEFAULT_TEMP_OUTDOOR_MEDIUM, -15, 10, 0.5, "°C"),
    CONF_EXTRA_FILTER_DURATION: (DEFAULT_EXTRA_FILTER_DURATION, 5, 480, 5, "min"),
    CONF_COOLDOWN_MINUTES: (DEFAULT_COOLDOWN_MINUTES, 0, 60, 1, "min"),
    CONF_MIN_ON_MINUTES: (DEFAULT_MIN_ON_MINUTES, 0, 60, 1, "min"),
    CONF_HP_TEMP_BEST_PRICE: (DEFAULT_HP_TEMP_BEST_PRICE, 15, 40, 0.5, "°C"),
    CONF_HP_TEMP_NORMAL: (DEFAULT_HP_TEMP_NORMAL, 15, 40, 0.5, "°C"),
    CONF_FEEDBACK_THRESHOLD: (DEFAULT_FEEDBACK_THRESHOLD, 0, 5000, 1.0, None),
    CONF_FEEDBACK_MEDIUM_FROM: (DEFAULT_FEEDBACK_SPEED_FROM, 0, 5000, 1.0, None),
    CONF_FEEDBACK_HIGH_FROM: (DEFAULT_FEEDBACK_SPEED_FROM, 0, 5000, 1.0, None),
}
_INT_KEYS = {k for k, v in {**_MAIN_NUMBERS, **_ADVANCED_NUMBERS}.items() if isinstance(v[3], int)}


def _number(spec: tuple) -> NumberSelector:
    _, lo, hi, step, unit = spec
    config = {"min": lo, "max": hi, "step": step, "mode": NumberSelectorMode.BOX}
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**config))


def _suggest(cfg: dict, key: str) -> dict:
    value = cfg.get(key)
    return {"suggested_value": value} if value else {}


def _schema(cfg: dict, with_name: bool) -> vol.Schema:
    fields: dict = {}
    if with_name:
        fields[vol.Required("name", default=cfg.get("name", DEFAULT_NAME))] = TextSelector()

    for key, selector in (
        (CONF_SWITCH_RPM_LOW, _SWITCH),
        (CONF_SWITCH_RPM_MEDIUM, _SWITCH),
        (CONF_SWITCH_RPM_HIGH, _SWITCH),
        (CONF_SENSOR_PRICE, _PRICE),
        (CONF_SENSOR_POOL_TEMP, _TEMP),
        (CONF_SENSOR_OUTDOOR_TEMP, _TEMP),
    ):
        fields[vol.Required(key, description=_suggest(cfg, key))] = selector

    for key, spec in _MAIN_NUMBERS.items():
        fields[vol.Required(key, default=cfg.get(key, spec[0]))] = _number(spec)

    optional = {
        vol.Optional(CONF_SWITCH_CIRCULATION, description=_suggest(cfg, CONF_SWITCH_CIRCULATION)): _SWITCH,
        vol.Optional(CONF_SENSOR_PUMP_FEEDBACK, description=_suggest(cfg, CONF_SENSOR_PUMP_FEEDBACK)):
            EntitySelector(EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_SENSOR_PUMP_RPM, description=_suggest(cfg, CONF_SENSOR_PUMP_RPM)):
            EntitySelector(EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_SENSOR_PUMP_POWER, description=_suggest(cfg, CONF_SENSOR_PUMP_POWER)):
            EntitySelector(EntitySelectorConfig(domain="sensor", device_class="power")),
        vol.Optional(CONF_CLIMATE_HEAT_PUMP, description=_suggest(cfg, CONF_CLIMATE_HEAT_PUMP)):
            EntitySelector(EntitySelectorConfig(domain="climate")),
        vol.Optional(CONF_SWITCH_UV_LAMP, description=_suggest(cfg, CONF_SWITCH_UV_LAMP)): _SWITCH,
        vol.Optional(CONF_COVER_POOL, description=_suggest(cfg, CONF_COVER_POOL)):
            EntitySelector(EntitySelectorConfig(domain="cover")),
    }
    fields[vol.Required(SECTION_OPTIONAL)] = section(
        vol.Schema(optional), {"collapsed": not any(cfg.get(k) for k in OPTIONAL_ENTITIES)}
    )

    advanced = {
        vol.Required(key, default=cfg.get(key, spec[0])): _number(spec)
        for key, spec in _ADVANCED_NUMBERS.items()
    }
    fields[vol.Required(SECTION_ADVANCED)] = section(vol.Schema(advanced), {"collapsed": True})
    return vol.Schema(fields)


def _flatten(user_input: dict) -> dict:
    """Lift section values to the top level, cast whole numbers, blank cleared entities."""
    data = {k: v for k, v in user_input.items() if k not in (SECTION_OPTIONAL, SECTION_ADVANCED)}
    data.update(user_input.get(SECTION_OPTIONAL, {}))
    data.update(user_input.get(SECTION_ADVANCED, {}))
    for key in OPTIONAL_ENTITIES:
        # Store cleared optional entities explicitly, otherwise an older value
        # in the entry's data would show through the options.
        data.setdefault(key, "")
    for key in _INT_KEYS:
        if key in data:
            data[key] = int(data[key])
    return data


def _validate(data: dict) -> dict:
    speeds = [data.get(k) for k in (CONF_SWITCH_RPM_LOW, CONF_SWITCH_RPM_MEDIUM, CONF_SWITCH_RPM_HIGH)]
    if len(set(speeds)) != 3:
        return {"base": "duplicate_speed_switch"}
    if data.get(CONF_SENSOR_POOL_TEMP) == data.get(CONF_SENSOR_OUTDOOR_TEMP):
        return {"base": "same_temperature_sensor"}
    return {}


class PoolCirculationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Circulation."""

    VERSION = 2

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict = {}
        if user_input is not None:
            data = _flatten(user_input)
            errors = _validate(data)
            if not errors:
                return self.async_create_entry(title=data.pop("name", DEFAULT_NAME), data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(_flatten(user_input) if user_input else {}, with_name=True),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return PoolCirculationOptionsFlow()


class PoolCirculationOptionsFlow(config_entries.OptionsFlow):
    """Change entities and settings after setup."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        errors: dict = {}
        cfg = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            data = _flatten(user_input)
            errors = _validate(data)
            if not errors:
                return self.async_create_entry(title="", data=data)
            cfg = {**cfg, **data}

        return self.async_show_form(step_id="init", data_schema=_schema(cfg, with_name=False), errors=errors)

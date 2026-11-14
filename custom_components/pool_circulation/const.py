"""Constants for Pool Circulation integration."""

DOMAIN = "pool_circulation"
VERSION = "1.12.0"

# ---------------------------------------------------------------------------
# Configuration keys — entities
# ---------------------------------------------------------------------------
# Required
CONF_SWITCH_RPM_LOW = "switch_rpm_low"
CONF_SWITCH_RPM_MEDIUM = "switch_rpm_medium"
CONF_SWITCH_RPM_HIGH = "switch_rpm_high"
CONF_SENSOR_PRICE = "sensor_price"
CONF_SENSOR_POOL_TEMP = "sensor_pool_temp"
CONF_SENSOR_OUTDOOR_TEMP = "sensor_outdoor_temp"
# Optional
CONF_SWITCH_CIRCULATION = "switch_circulation"   # separate pump on/off switch
CONF_CLIMATE_HEAT_PUMP = "climate_heat_pump"
CONF_SWITCH_UV_LAMP = "switch_uv_lamp"
CONF_COVER_POOL = "cover_pool"
# Power (W) or rpm sensor showing whether the pump really turns. The speed
# switches only say what the controller was told, not what the pump does.
CONF_SENSOR_PUMP_FEEDBACK = "sensor_pump_feedback"
# Extra readings shown on the card. The power sensor is also used for kWh and
# cost when the feedback sensor itself doesn't measure power.
CONF_SENSOR_PUMP_RPM = "sensor_pump_rpm"
CONF_SENSOR_PUMP_POWER = "sensor_pump_power"

REQUIRED_ENTITIES = (
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    CONF_SWITCH_RPM_HIGH,
    CONF_SENSOR_PRICE,
    CONF_SENSOR_POOL_TEMP,
    CONF_SENSOR_OUTDOOR_TEMP,
)
OPTIONAL_ENTITIES = (
    CONF_SWITCH_CIRCULATION,
    CONF_CLIMATE_HEAT_PUMP,
    CONF_SWITCH_UV_LAMP,
    CONF_COVER_POOL,
    CONF_SENSOR_PUMP_FEEDBACK,
    CONF_SENSOR_PUMP_RPM,
    CONF_SENSOR_PUMP_POWER,
)
ALL_ENTITIES = REQUIRED_ENTITIES + OPTIONAL_ENTITIES

# ---------------------------------------------------------------------------
# Configuration keys — settings
# ---------------------------------------------------------------------------
CONF_DAILY_HOURS = "daily_hours"
CONF_TEMP_ALGAE_THRESHOLD = "temp_algae_threshold"
CONF_TEMP_FREEZE_THRESHOLD = "temp_freeze_threshold"
# Advanced
CONF_POOL_CRITICAL_TEMP = "pool_critical_temp"   # pool ≤ this → circulate continuously
CONF_TEMP_OUTDOOR_MEDIUM = "temp_outdoor_medium" # pool critical: medium below this outdoor temp
CONF_EXTRA_FILTER_DURATION = "extra_filter_duration"
CONF_COOLDOWN_MINUTES = "cooldown_minutes"
CONF_MIN_ON_MINUTES = "min_on_minutes"
CONF_HP_TEMP_BEST_PRICE = "hp_temp_best_price"
CONF_HP_TEMP_NORMAL = "hp_temp_normal"
CONF_FEEDBACK_THRESHOLD = "feedback_threshold"   # feedback above this = pump running
CONF_FEEDBACK_MEDIUM_FROM = "feedback_medium_from"  # feedback at/above this = medium speed
CONF_FEEDBACK_HIGH_FROM = "feedback_high_from"      # feedback at/above this = high speed

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_NAME = "Pool Circulation"
DEFAULT_DAILY_HOURS = 8
DEFAULT_TEMP_ALGAE_THRESHOLD = 7.0   # °C pool — algae don't grow below this
DEFAULT_TEMP_FREEZE_THRESHOLD = 2.0  # °C outdoor — frost guard: low speed at or below
DEFAULT_POOL_CRITICAL_TEMP = 4.0     # °C pool — approaching 3°C the circulation can fail
DEFAULT_TEMP_OUTDOOR_MEDIUM = 0.0    # °C outdoor — pool critical runs medium below this
DEFAULT_SAFETY_HYSTERESIS = 0.5      # °C — guards release this far above the threshold
DEFAULT_EXTRA_FILTER_DURATION = 60   # minutes
DEFAULT_COOLDOWN_MINUTES = 10        # minutes between pump off → on
DEFAULT_MIN_ON_MINUTES = 10          # minimum minutes pump stays on once started
DEFAULT_HP_TEMP_BEST_PRICE = 31.0    # °C — heat pump target during cheap hours
DEFAULT_HP_TEMP_NORMAL = 30.0        # °C — heat pump target otherwise
DEFAULT_FEEDBACK_THRESHOLD = 10.0    # W or rpm
DEFAULT_FEEDBACK_SPEED_FROM = 0.0    # 0 = pick by the feedback sensor's unit

# Speed bands used when the medium/high limits are left at 0, by the feedback
# sensor's unit: (medium from, high from)
AUTO_SPEED_BANDS = {
    "w": (300.0, 500.0),
    "rpm": (2000.0, 2650.0),
}

# Feedback must disagree with the switches this long before it counts as a fault
# (the inverter ramps up / down), and a stuck pump is re-commanded this often.
FEEDBACK_GRACE_SECONDS = 180
FAULT_RETRY_MINUTES = 10
# Speed controllers often start at full speed and settle on the selected speed
# after a few minutes. A different measured speed only counts as wrong after this.
SPEED_SETTLE_SECONDS = 360

# Summer: the priciest N hours of the day count as peak (no catch-up at medium)
PEAK_HOURS = 4

# ---------------------------------------------------------------------------
# Circulation modes
# ---------------------------------------------------------------------------
MODE_OFF = "off"
MODE_LOW = "low"
MODE_MEDIUM = "medium"
MODE_HIGH = "high"

ALL_MODES = [MODE_OFF, MODE_LOW, MODE_MEDIUM, MODE_HIGH]

# ---------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------
SEASON_SUMMER = "summer"
SEASON_WINTER = "winter"

# ---------------------------------------------------------------------------
# Entities this integration creates, by unique_id suffix. Anything else
# registered for the config entry is left over from an older version and
# removed on setup.
# ---------------------------------------------------------------------------
ENTITY_SUFFIXES = {
    "sensor": ("mode", "reason", "hours_today", "hours_remaining"),
    "binary_sensor": ("pump_fault",),
    "switch": ("automation", "extra_filter", "winter_mode"),
    "number": ("daily_hours", "algae_threshold", "freeze_threshold", "extra_filter_duration"),
}

# ---------------------------------------------------------------------------
# HA events
# ---------------------------------------------------------------------------
EVENT_MODE_CHANGED = "pool_circulation_mode_changed"
EVENT_UV_CHANGED = "pool_circulation_uv_changed"
EVENT_EXTRA_FILTER_CHANGED = "pool_circulation_extra_filter_changed"
EVENT_PUMP_FAULT = "pool_circulation_pump_fault"

# ---------------------------------------------------------------------------
# Persistent storage
# ---------------------------------------------------------------------------
STORE_VERSION = 1
STORE_KEY = "pool_circulation"

# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------
COVER_BURST_MINUTES = 5
SAVE_INTERVAL_MINUTES = 5          # runtime counters are persisted this often
MAX_ACCOUNTING_GAP_SECONDS = 300   # longer gaps (HA down) are not counted as runtime

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
CARD_URL = "/pool_circulation/pool-circulation-card.js"

"""Constants for Pool Circulation integration."""

DOMAIN = "pool_circulation"

# ---------------------------------------------------------------------------
# Configuration keys
# ---------------------------------------------------------------------------
CONF_CLIMATE_HEAT_PUMP = "climate_heat_pump"
CONF_SWITCH_CIRCULATION = "switch_circulation"
CONF_SWITCH_RPM_LOW = "switch_rpm_low"
CONF_SWITCH_RPM_MEDIUM = "switch_rpm_medium"
CONF_SWITCH_RPM_HIGH = "switch_rpm_high"
CONF_RPM_LOW = "rpm_low"
CONF_RPM_MEDIUM = "rpm_medium"
CONF_RPM_HIGH = "rpm_high"
CONF_SENSOR_PRICE = "sensor_price"
CONF_SENSOR_PRICE_LEVEL = "sensor_price_level"
CONF_BINARY_BEST_PRICE = "binary_best_price"
CONF_BINARY_PEAK_PRICE = "binary_peak_price"
CONF_DAILY_HOURS = "daily_hours"
CONF_SENSOR_OUTDOOR_TEMP = "sensor_outdoor_temp"
CONF_SENSOR_POOL_TEMP = "sensor_pool_temp"
CONF_TEMP_ALGAE_THRESHOLD = "temp_algae_threshold"
CONF_TEMP_FREEZE_THRESHOLD = "temp_freeze_threshold"
CONF_SWITCH_UV_LAMP = "switch_uv_lamp"
CONF_COVER_POOL = "cover_pool"
CONF_EXTRA_FILTER_DURATION = "extra_filter_duration"
CONF_SENSOR_ACTUAL_RPM = "sensor_actual_rpm"
CONF_COOLDOWN_MINUTES = "cooldown_minutes"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_NAME = "Pool Circulation"
DEFAULT_DAILY_HOURS = 8
DEFAULT_TEMP_ALGAE_THRESHOLD = 8.0   # °C — algae don't grow below this
DEFAULT_TEMP_FREEZE_THRESHOLD = 2.0  # °C — circulate to prevent freezing
DEFAULT_RPM_LOW = 1000
DEFAULT_RPM_MEDIUM = 2000
DEFAULT_RPM_HIGH = 3000
DEFAULT_EXTRA_FILTER_DURATION = 60   # minutes
DEFAULT_COOLDOWN_MINUTES = 10        # minutes between pump off → on

# Default entity IDs (Trulsibrunn Nordpool integration)
DEFAULT_SENSOR_PRICE = "sensor.trulsibrunn_timpris_aktuell"
DEFAULT_SENSOR_PRICE_LEVEL = "sensor.trulsibrunn_aktuell_timprisniva"
DEFAULT_BINARY_BEST_PRICE = "binary_sensor.trulsibrunn_basta_prisperiod"
DEFAULT_BINARY_PEAK_PRICE = "binary_sensor.trulsibrunn_topprisperiod"

# ---------------------------------------------------------------------------
# Circulation modes
# ---------------------------------------------------------------------------
MODE_OFF = "off"
MODE_LOW = "low"
MODE_MEDIUM = "medium"
MODE_HIGH = "high"

ALL_MODES = [MODE_OFF, MODE_LOW, MODE_MEDIUM, MODE_HIGH]

# ---------------------------------------------------------------------------
# HA events
# ---------------------------------------------------------------------------
EVENT_MODE_CHANGED = "pool_circulation_mode_changed"
EVENT_UV_CHANGED = "pool_circulation_uv_changed"
EVENT_EXTRA_FILTER_CHANGED = "pool_circulation_extra_filter_changed"

# ---------------------------------------------------------------------------
# Persistent storage
# ---------------------------------------------------------------------------
STORE_VERSION = 1
STORE_KEY = "pool_circulation"

# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------
COORDINATOR_UPDATE_INTERVAL = 60  # seconds

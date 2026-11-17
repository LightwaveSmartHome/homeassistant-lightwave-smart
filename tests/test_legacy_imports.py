"""Execute legacy import fallbacks that current Home Assistant never takes."""

import importlib
import sys


def _reload_climate_fallbacks() -> None:
    """Import climate.py through both legacy ImportError branches.

    The unconditional import needs ClimateEntityFeature to stay on the climate
    package. Only the modern const names are hidden, and the old names are
    injected so the except imports can finish.
    """
    import homeassistant.components.climate.const as climate_const
    import homeassistant.const as ha_const

    full = "custom_components.lightwave_smart.climate"
    saved = sys.modules.get(full)
    stashed = {}
    injected = []

    def hide(module, name):
        if name in module.__dict__:
            stashed[(module, name)] = module.__dict__[name]
            delattr(module, name)

    def inject(module, name, value):
        if name not in module.__dict__:
            setattr(module, name, value)
            injected.append((module, name))

    try:
        hide(climate_const, "HVACAction")
        hide(climate_const, "HVACMode")
        for name, value in (
            ("CURRENT_HVAC_HEAT", "heating"),
            ("CURRENT_HVAC_IDLE", "idle"),
            ("CURRENT_HVAC_OFF", "off"),
            ("HVAC_MODE_HEAT", "heat"),
            ("HVAC_MODE_OFF", "off"),
            ("SUPPORT_PRESET_MODE", 16),
            ("SUPPORT_TARGET_HUMIDITY", 4),
            ("SUPPORT_TARGET_TEMPERATURE", 1),
        ):
            inject(climate_const, name, value)
        hide(ha_const, "UnitOfTemperature")
        inject(ha_const, "TEMP_CELSIUS", "°C")
        inject(ha_const, "TEMP_FAHRENHEIT", "°F")
        sys.modules.pop(full, None)
        importlib.import_module(full)
    finally:
        for module, name in injected:
            if name in module.__dict__:
                delattr(module, name)
        for (module, name), value in stashed.items():
            setattr(module, name, value)
        if saved is not None:
            sys.modules[full] = saved
        else:
            sys.modules.pop(full, None)


def _reload_blocking(module_name: str, prepare) -> None:
    full = f"custom_components.lightwave_smart.{module_name}"
    saved = sys.modules.get(full)
    prepare()
    sys.modules.pop(full, None)
    try:
        importlib.import_module(full)
    except ImportError:
        pass
    finally:
        prepare(True)
        if saved is not None:
            sys.modules[full] = saved
        else:
            sys.modules.pop(full, None)


def test_sensor_binary_and_climate_fallbacks():
    import homeassistant.components.binary_sensor as binary_sensor
    import homeassistant.components.climate as climate
    import homeassistant.components.cover as cover
    import homeassistant.components.sensor as sensor
    import homeassistant.const as ha_const

    sensor_names = ["SensorStateClass", "SensorDeviceClass"]
    const_names = [
        "UnitOfTemperature",
        "UnitOfElectricCurrent",
        "UnitOfElectricPotential",
        "UnitOfEnergy",
        "UnitOfPower",
    ]
    binary_names = ["BinarySensorDeviceClass"]
    climate_names = ["HVACAction", "HVACMode", "ClimateEntityFeature"]
    cover_names = ["CoverEntityFeature"]
    saved = {}

    def stash(module, names):
        for name in names:
            if name in module.__dict__:
                saved[(module, name)] = module.__dict__[name]
                delattr(module, name)

    def restore():
        for (module, name), value in saved.items():
            setattr(module, name, value)
        saved.clear()

    def prepare(restore_flag=False):
        if restore_flag:
            restore()
            return
        stash(sensor, sensor_names)
        stash(ha_const, const_names)
        stash(binary_sensor, binary_names)
        stash(climate, climate_names)
        # climate.const re-exports the same names in older cores.
        try:
            import homeassistant.components.climate.const as climate_const
        except ImportError:
            climate_const = None
        if climate_const is not None:
            stash(climate_const, climate_names)
        stash(cover, cover_names)

    _reload_blocking("sensor", prepare)
    _reload_blocking("binary_sensor", prepare)
    _reload_blocking("cover", prepare)
    _reload_climate_fallbacks()

    import homeassistant.components.climate as climate_mod

    had_native = "native_temperature_unit" in vars(climate_mod.ClimateEntity)
    if had_native:
        removed = climate_mod.ClimateEntity.__dict__["native_temperature_unit"]
        delattr(climate_mod.ClimateEntity, "native_temperature_unit")
    else:
        climate_mod.ClimateEntity.native_temperature_unit = "°C"
        removed = None
    full = "custom_components.lightwave_smart.climate"
    saved_climate = sys.modules.get(full)
    sys.modules.pop(full, None)
    try:
        reloaded = importlib.import_module(full)
        cls = reloaded.LWRF2Climate
        entity = object.__new__(cls)
        entity._temperature = 21.0
        entity._target_temperature = 19.5
        if "native_current_temperature" in cls.__dict__:
            assert entity.native_current_temperature == 21.0
            assert entity.native_target_temperature == 19.5
        if "current_temperature" in cls.__dict__:
            assert entity.current_temperature == 21.0
            assert entity.target_temperature == 19.5
    finally:
        if had_native:
            climate_mod.ClimateEntity.native_temperature_unit = removed
        else:
            delattr(climate_mod.ClimateEntity, "native_temperature_unit")
        if saved_climate is not None:
            sys.modules[full] = saved_climate

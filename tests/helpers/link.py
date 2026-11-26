"""In-memory Lightwave link built from the real lightwave_smart device model."""

from __future__ import annotations

from lightwave_smart.device import LWRFDevice, LWRFFeature, LWRFFeatureSet


class FakeWebsocket:
    """Stand-in for LWWebsocket. Records writes and never opens a connection."""

    def __init__(self) -> None:
        self._connected = False
        self.sent: list = []
        self._connect_callbacks: list = []
        self._disconnect_callbacks: list = []
        self.deactivate_error: Exception | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = bool(value)

    def activate(self, source: str | None = None) -> None:
        return None

    def register_connect_callback(self, callback) -> None:
        if callback not in self._connect_callbacks:
            self._connect_callbacks.append(callback)

    def unregister_connect_callback(self, callback) -> None:
        if callback in self._connect_callbacks:
            self._connect_callbacks.remove(callback)

    def register_disconnect_callback(self, callback) -> None:
        if callback not in self._disconnect_callbacks:
            self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback) -> None:
        if callback in self._disconnect_callbacks:
            self._disconnect_callbacks.remove(callback)

    async def async_connect(self, *args, **kwargs) -> bool:
        self._connected = True
        for callback in list(self._connect_callbacks):
            result = callback()
            if hasattr(result, "__await__"):
                await result
        return True

    async def async_deactivate(self, source: str | None = None) -> None:
        if self.deactivate_error is not None:
            raise self.deactivate_error
        self._connected = False

    async def async_sendmessage(self, message, *args, **kwargs):
        self.sent.append(message)
        return [{"success": True, "payload": {"value": 1}}]

    def fire_disconnect(self) -> None:
        self._connected = False
        for callback in list(self._disconnect_callbacks):
            callback()


def add_feature(link, featureset, feature_type: str, state, feature_id: str | None = None):
    feature_id = feature_id or f"{featureset.featureset_id}-{feature_type}"
    feature = LWRFFeature(feature_id, {"attributes": {"type": feature_type}}, link)
    feature._state = state
    feature.decoded_obj = None
    feature.add_feature_set(featureset)
    featureset.features[feature_type] = feature
    link.features[feature_id] = feature
    return feature


def add_device(
    link,
    device_id: str,
    product_code: str,
    *,
    manufacturer: str = "LightwaveRF",
    virtual: str | None = None,
    serial: str = "SER",
    firmware: str = "1.0.0",
    latest: str = "1.2.0",
    room: str | None = None,
):
    device = LWRFDevice()
    device.link = link
    device.device_id = device_id
    device.product_code = product_code
    device.manufacturer_code = manufacturer
    device.virtual_product_code = virtual
    device.serial = serial
    device.firmware_version = firmware
    device.latest_firmware_version = latest
    device.latest_firmware_release_summary = "Fixes"
    device._latest_firmware_release_id = "rel-1"
    device.room_name = room
    device.connection_status = "connected"
    link.devices[device_id] = device
    return device


def add_featureset(link, device, featureset_id: str, name: str, features: dict, room: str | None = None):
    featureset = LWRFFeatureSet()
    featureset.link = link
    featureset.featureset_id = featureset_id
    featureset.name = name
    featureset.device = device
    featureset.room_name = room
    device.add_featureset(featureset)
    link.featuresets[featureset_id] = featureset
    for feature_type, state in features.items():
        add_feature(link, featureset, feature_type, state)
    return featureset


def populate_hierarchy(link) -> None:
    """Devices and featuresets that exercise every platform."""
    link.set_structure_data("s1-hub", "s1-hub", "Home")

    hub = add_device(link, "s1-hubdev", "L2", serial="HUB", room="Hall")
    add_featureset(
        link,
        hub,
        "s1-hub",
        "Link Plus",
        {
            "dawnTime": 25200,
            "duskTime": 64800,
            "year": 2026,
            "month": 11,
            "day": 16,
            "power": 4,
        },
        room="Hall",
    )

    dimmer = add_device(link, "s1-dimmer", "L21", room="Hall")
    add_featureset(
        link,
        dimmer,
        "s1-dimmer-a",
        "Hall (primary)",
        {
            "switch": 1,
            "dimLevel": 50,
            "rgbColor": 0xFF8040,
            "uiButtonPair": 0,
            "power": 12,
            "energy": 100,
            "temperature": 210,
            "rssi": -50,
            "lightLevel": 40,
        },
        room="Hall",
    )
    add_featureset(
        link,
        dimmer,
        "s1-dimmer-b",
        "Hall 2",
        {"switch": 0, "dimLevel": 0, "rgbColor": 0x101010},
    )

    driver = add_device(link, "s1-driver", "L84", room="Lounge")
    add_featureset(
        link,
        driver,
        "s1-driver-fs",
        "Strip",
        {
            "switch": 1,
            "dimLevel": 80,
            "colourHue": 120,
            "colourSaturation": 50,
            "colourTemperature": 200,
            "colourSetup": 3,
            "colourOutputMap": 0x0000FF21,
        },
    )

    socket = add_device(link, "s1-socket", "L42", room="Kitchen")
    add_featureset(
        link,
        socket,
        "s1-socket-fs",
        "Kettle",
        {
            "switch": 1,
            "socketSetup": 1,
            "outletInUse": 1,
            "uiButton": 0,
            "protection": 1,
            "batteryLevel": 80,
            "voltage": 230,
            "current": 10,
        },
    )

    gen2_switch = add_device(link, "s1-switch", "L22")
    add_featureset(
        link,
        gen2_switch,
        "s1-switch-fs",
        "Porch",
        {"switch": 0, "uiButton": 0},
    )

    gen1 = add_device(link, "s1-gen1", "LW400", manufacturer="JSJS")
    add_featureset(link, gen1, "s1-gen1-fs", "Old switch", {"switch": 1})

    cover = add_device(link, "s1-cover", "L51")
    add_featureset(link, cover, "s1-cover-fs", "Blind", {"threeWayRelay": 0})

    trv = add_device(link, "s1-trv", "L92")
    add_featureset(
        link,
        trv,
        "s1-trv-fs",
        "Radiator",
        {
            "targetTemperature": 190,
            "temperature": 180,
            "valveLevel": 40,
            "heatState": 1,
            "valveSetup": 1,
            "humidity": 55,
            "targetHumidity": 50,
        },
    )

    stat = add_device(link, "s1-stat", "L24")
    add_featureset(
        link,
        stat,
        "s1-stat-fs",
        "Thermostat",
        {"targetTemperature": 200, "temperature": 190, "callForHeat": 1},
    )

    window = add_device(link, "s1-window", "LW600", manufacturer="JSJS")
    add_featureset(
        link,
        window,
        "s1-window-fs",
        "Window",
        {"windowPosition": 1, "movement": 0, "uiDigitalInput": 1},
    )

    mk4 = add_device(link, "s1-mk4", "L21")
    add_featureset(
        link,
        mk4,
        "s1-mk4-fs",
        "MK4",
        {
            "switch": 1,
            "dimLevel": 40,
            "rgbColor": 10,
            "uiLEDOnColour": 111,
            "uiLEDOffColour": 222,
        },
    )

    broken = add_device(link, "s1-broken", "L21")
    add_featureset(link, broken, "s1-broken-fs", "Broken", {"dimLevel": 10})

    class _RaisingDevice(LWRFDevice):
        """product_code is readable; manufacturer_code raises after construction."""

        def __init__(self):
            self.link = None
            self.device_id = None
            self.featuresets = []
            self.featureIds = []
            self._firmware_event_callbacks = []
            self._connection_status_callbacks = []
            self.virtual_product_code = None
            self.firmware_version = "1.0.0"
            self.serial = "SER"
            self.latest_firmware_version = "1.2.0"
            self.latest_firmware_release_summary = "Fixes"
            self._latest_firmware_release_id = "rel-1"
            self.firmware_update_in_progress = False
            self.room_name = None
            self.connection_status = "connected"

        @property
        def manufacturer_code(self):
            raise RuntimeError("manufacturer")

        @property
        def product_code(self):
            return "L84"

    raising = _RaisingDevice()
    raising.link = link
    raising.device_id = "s1-raising"
    raising.featuresets = []
    raising.connection_status = "connected"
    link.devices[raising.device_id] = raising
    select_fs = LWRFFeatureSet()
    select_fs.link = link
    select_fs.featureset_id = "s1-raising-fs"
    select_fs.name = "Bad select"
    select_fs.device = raising
    raising.add_featureset(select_fs)
    link.featuresets[select_fs.featureset_id] = select_fs
    for feature_type, state in {
        "colourHue": 1,
        "colourSetup": 0,
        "colourTemperature": 200,
        "targetTemperature": 200,
    }.items():
        add_feature(link, select_fs, feature_type, state)

    class _RaisingState:
        def __init__(self, feature):
            self._feature = feature

        @property
        def state(self):
            raise RuntimeError("color")

        def __getattr__(self, name):
            return getattr(self._feature, name)

    bad_led = add_device(link, "s1-badled", "L21")
    bad_led_fs = add_featureset(
        link,
        bad_led,
        "s1-badled-fs",
        "Bad LED",
        {"rgbColor": 1},
    )
    raw = bad_led_fs.features["rgbColor"]
    bad_led_fs.features["rgbColor"] = _RaisingState(raw)


def separate_hub_per_link(link_script) -> None:
    """Give every link after the first its own hub, like a second Lightwave login."""

    def after_prepare(link):
        index = len(link_script.links)
        if index == 0:
            return
        hub = link.featuresets.pop("s1-hub")
        hub.featureset_id = f"s{index + 1}-hub"
        hub.device.serial = f"HUB{index + 1}"
        link.featuresets[hub.featureset_id] = hub
        link.set_structure_data(hub.featureset_id, hub.featureset_id, "Cottage")

    link_script.after_prepare = after_prepare


def prepare_link(link) -> FakeWebsocket:
    websocket = FakeWebsocket()
    link._ws = websocket
    populate_hierarchy(link)

    async def _noop(*args, **kwargs):
        return None

    link.async_get_hierarchy = _noop
    link.async_update_featureset_states = _noop
    return websocket

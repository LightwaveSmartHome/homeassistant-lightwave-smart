"""Tests for LWRF2LinkBinarySensor last_event attribute and refresh cadence."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import custom_components.lightwave_smart.binary_sensor as bs_mod
from custom_components.lightwave_smart.binary_sensor import (
    LWRF2LinkBinarySensor,
    _LINK_LAST_EVENT_REFRESH_INTERVAL,
)


class FakeLink:
    def __init__(self, featureset_id="s1-hubgrp"):
        self.connected = True
        self.last_event = None
        self.connect_callbacks = []
        self.disconnect_callbacks = []
        self.connection_callbacks = []
        self.latched = False

        device = SimpleNamespace(
            device_id="s1-1-99+0",
            manufacturer_code="LightwaveRF",
            product_code="L2",
            room_name=None,
            is_hub=lambda: True,
            is_gen2=lambda: True,
        )
        featureset = SimpleNamespace(
            featureset_id=featureset_id,
            device=device,
            room_name=None,
            features={"dawnTime": SimpleNamespace(state=25200)},
            is_gen2=lambda: True,
        )
        self.featuresets = {featureset_id: featureset}

    def get_linkPlus_featureset_id(self, _thing_id):
        return "s1-hubgrp"

    def get_link_last_event(self, featureset_id):
        assert featureset_id == "s1-hubgrp"
        return self.last_event

    def register_connect_callback(self, cb):
        if cb not in self.connect_callbacks:
            self.connect_callbacks.append(cb)

    def unregister_connect_callback(self, cb):
        if cb in self.connect_callbacks:
            self.connect_callbacks.remove(cb)

    def register_disconnect_callback(self, cb):
        if cb not in self.disconnect_callbacks:
            self.disconnect_callbacks.append(cb)

    def unregister_disconnect_callback(self, cb):
        if cb in self.disconnect_callbacks:
            self.disconnect_callbacks.remove(cb)

    def link_is_latched_offline(self, _device_id):
        return self.latched

    async def async_register_connection_status_callback(self, device_id, callback):
        pair = (device_id, callback)
        if pair not in self.connection_callbacks:
            self.connection_callbacks.append(pair)

    def unregister_connection_status_callback(self, device_id, callback):
        pair = (device_id, callback)
        if pair in self.connection_callbacks:
            self.connection_callbacks.remove(pair)


def test_extra_state_attributes_omits_last_event_until_first_notification():
    link = FakeLink()
    sensor = LWRF2LinkBinarySensor("Hub", "s1-hubgrp", link)

    assert sensor.extra_state_attributes == {"lwrf_dawnTime": 25200}

    ts = datetime(2026, 11, 14, 18, 25, 30, tzinfo=timezone.utc)
    link.last_event = ts
    assert sensor.extra_state_attributes == {
        "lwrf_dawnTime": 25200,
        "last_event": "2026-11-14T18:25:30+00:00",
    }


@pytest.mark.asyncio
async def test_periodic_refresh_writes_only_on_change_and_cleans_up(monkeypatch):
    tracked = {}
    unsubbed = {"count": 0}

    def fake_track_time_interval(hass, action, interval):
        tracked["hass"] = hass
        tracked["action"] = action
        tracked["interval"] = interval

        def _unsub():
            unsubbed["count"] += 1

        return _unsub

    monkeypatch.setattr(bs_mod, "async_track_time_interval", fake_track_time_interval)

    link = FakeLink()
    sensor = LWRF2LinkBinarySensor("Hub", "s1-hubgrp", link)
    sensor.hass = object()

    writes = []
    scheduled = []
    sensor.async_write_ha_state = lambda: writes.append(
        (sensor.is_on, sensor.extra_state_attributes.get("last_event"))
    )
    sensor.async_schedule_update_ha_state = lambda *args: scheduled.append(sensor.is_on)

    await sensor.async_added_to_hass()
    assert tracked["interval"] == _LINK_LAST_EVENT_REFRESH_INTERVAL
    assert len(link.connect_callbacks) == 1
    assert len(link.disconnect_callbacks) == 1
    assert link.connection_callbacks == [("s1-1-99+0", sensor._async_link_changed)]

    tracked["action"](None)
    assert writes == []

    # Inbound event advances last_event -> next 60s tick writes once, following idle tick does not.
    link.last_event = datetime(2026, 11, 14, 19, 0, 0, tzinfo=timezone.utc)
    tracked["action"](None)
    assert writes == [(True, "2026-11-14T19:00:00+00:00")]

    tracked["action"](None)
    assert len(writes) == 1

    # Suppressed disconnect followed by failed reconnect is caught on the next 60s tick.
    link.connected = False
    tracked["action"](None)
    assert writes[-1] == (False, "2026-11-14T19:00:00+00:00")

    # Push callback updates snapshot so the next tick does not double-write.
    link.connected = True
    link.last_event = datetime(2026, 11, 14, 19, 1, 0, tzinfo=timezone.utc)
    sensor._async_link_changed()
    assert scheduled == [True]
    tracked["action"](None)
    assert len(writes) == 2

    await sensor.async_will_remove_from_hass()
    assert unsubbed["count"] == 1
    assert link.connect_callbacks == []
    assert link.disconnect_callbacks == []
    assert link.connection_callbacks == []


@pytest.mark.asyncio
async def test_cloud_link_sensor_with_real_lwlink2_across_reconnect(monkeypatch):
    from lightwave_smart.device import LWRFDevice, LWRFFeature, LWRFFeatureSet
    from lightwave_smart.link import LWLink2
    import lightwave_smart.link as link_mod

    monkeypatch.setattr(bs_mod, "async_track_time_interval", lambda *_: lambda: None)
    t1 = datetime(2026, 11, 14, 20, 5, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(link_mod, "_utc_now", lambda: t1)

    link = LWLink2()
    hub_dev = LWRFDevice()
    hub_dev.link = link
    hub_dev.device_id = "s1-1-99+0"
    hub_dev.product_code = "L2"
    hub_dev.manufacturer_code = "LightwaveRF"
    hub_fs = LWRFFeatureSet()
    hub_fs.link = link
    hub_fs.featureset_id = "s1-hubgrp"
    hub_fs.device = hub_dev
    hub_dev.add_featureset(hub_fs)
    feat = LWRFFeature("s1-10-99+2", {"attributes": {"type": "power"}}, link)
    link.devices = {hub_dev.device_id: hub_dev}
    link.featuresets = {hub_fs.featureset_id: hub_fs}
    link.features = {feat.id: feat}

    sensor = LWRF2LinkBinarySensor("Hub", "s1-hubgrp", link)
    sensor.hass = object()
    updates = []
    sensor.async_schedule_update_ha_state = lambda *args: updates.append("push")

    await sensor.async_added_to_hass()
    assert "last_event" not in sensor.extra_state_attributes

    # Simulate service_handle_reconnect: async_deactivate then async_activate.
    async def fake_connect(max_tries=None, force_keep_alive_secs=0, source=None):
        link._ws._authenticated = True
        await link._ws._notify_connect()
        return True

    link._ws.async_connect = fake_connect
    try:
        await link.async_activate(source="initial")
        assert updates == ["push"]
        updates.clear()

        await link.async_deactivate(source="service_handle_reconnect")
        await link.async_activate(source="service_handle_reconnect")
        assert updates == ["push"]

        await link._feature_event_handler({
            "items": [{"payload": {"featureId": "s1-10-99+2", "value": 120}}]
        })
        assert sensor.extra_state_attributes["last_event"] == "2026-11-14T20:05:00+00:00"
    finally:
        await sensor.async_will_remove_from_hass()
        await link.async_deactivate(source="cleanup")
        assert hub_dev._connection_status_callbacks == []


@pytest.mark.asyncio
async def test_cloud_link_turns_off_when_the_hub_websocket_latches(monkeypatch):
    link = FakeLink()
    sensor = LWRF2LinkBinarySensor("Hub", "s1-hubgrp", link)
    sensor.hass = object()
    scheduled = []
    sensor.async_schedule_update_ha_state = lambda *args: scheduled.append(sensor.is_on)
    monkeypatch.setattr(bs_mod, "async_track_time_interval", lambda *_: lambda: None)

    await sensor.async_added_to_hass()
    assert sensor.is_on is True

    link.latched = True
    sensor._async_link_changed(device_id=link.featuresets["s1-hubgrp"].device.device_id)
    assert scheduled == [False]
    assert sensor.is_on is False

    link.latched = False
    link.connected = True
    sensor._async_link_changed()
    assert sensor.is_on is True

    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_cloud_link_follows_a_real_hub_websocket_latch(monkeypatch):
    from lightwave_smart.device import LWRFDevice, LWRFFeatureSet
    from lightwave_smart.link import LWLink2
    import lightwave_smart.link as link_mod

    monkeypatch.setattr(bs_mod, "async_track_time_interval", lambda *_: lambda: None)
    monkeypatch.setattr(link_mod, "_LINK_OFFLINE_GRACE_SECS", 0)

    link = LWLink2()
    hub_dev = LWRFDevice()
    hub_dev.link = link
    hub_dev.device_id = "s1-1-99+0"
    hub_dev.product_code = "L2"
    hub_dev.manufacturer_code = "LightwaveRF"
    hub_fs = LWRFFeatureSet()
    hub_fs.link = link
    hub_fs.featureset_id = "s1-hubgrp"
    hub_fs.device = hub_dev
    hub_dev.add_featureset(hub_fs)
    link.devices = {hub_dev.device_id: hub_dev}
    link.featuresets = {hub_fs.featureset_id: hub_fs}

    class _OpenSocket:
        closed = False

    link._ws._authenticated = True
    link._ws._websocket = _OpenSocket()

    sensor = LWRF2LinkBinarySensor("Hub", "s1-hubgrp", link)
    sensor.hass = object()
    scheduled = []
    sensor.async_schedule_update_ha_state = lambda *args: scheduled.append(sensor.is_on)

    await sensor.async_added_to_hass()
    try:
        assert sensor.is_on is True
        await link._link_socket_event_handler({
            "items": [{"payload": {"linkId": "99", "online": False}}]
        })
        await next(iter(link._link_grace_tasks.values()))
        assert sensor.is_on is False
        assert scheduled == [False]
    finally:
        await sensor.async_will_remove_from_hass()
        await link.async_deactivate(source="cleanup")


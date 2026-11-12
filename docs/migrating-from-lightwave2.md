# Moving from Lightwave2

Lightwave Smart replaces the [Lightwave2 integration](https://github.com/bigbadblunt/homeassistant-lightwave2). That project says it has known issues and is no longer maintained, and its readme points here.

This move is a breaking change. Lightwave2 uses the domain `lightwave2`. Lightwave Smart uses `lightwave_smart`, and its unique ids are different, so Home Assistant creates new devices and new entity ids. While Lightwave 2 is still installed, a name that is already taken gets a suffix, so the new switch may be `switch.lounge_lamp_2`. That id stays after you delete Lightwave 2.

The hardware limit is unchanged. You need a [Link Plus](https://lightwaverf.com/collections/all/products/link-plus) hub. Connect Series (generation 1) and Smart Series (generation 2) devices on that hub are both controlled. A Connect Series hub (Lightwave Link / WiFi Link) does not work.

## What changes

Anything that stored a Lightwave2 entity id needs to be pointed at the new entity:

- Automations, scripts, and scenes
- Dashboards, including the Energy dashboard if it uses Lightwave power or energy sensors
- Custom names, disabled entities, and area assignments on the old devices

History stays on the old entity ids. The new entities start their own history.

Read-only `lwrf_*` attributes are still on the entities. For generation 2 lights, brightness can still be set without turning the light on. The service is `lightwave_smart.set_brightness`, and brightness is 0 to 255.

| Lightwave2 | Lightwave Smart |
| --- | --- |
| `lightwave2.set_brightness` | `lightwave_smart.set_brightness` |
| `lightwave2.click` with a numeric code | An event entity on the device. A socket reports `Short.1` to `Short.5`, `Long`, and `Long-Release`. A dimmer or wirefree prefixes those with `Up.` or `Down.` (`Down.Short.2` is down, pressed twice). The same numeric codes are still fired as `lightwave_smart.click`. That event is deprecated and will be removed. |
| `lightwave2.reconnect` | `lightwave_smart.reconnect`. This should no longer be required. Open an [issue](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/issues) if the connection or device states still need a manual kick. |
| `lightwave2.update_states` | `lightwave_smart.update_states`. Same guidance as reconnect. |
| `lightwave2.whdelete` | Delete automations that called this. Lightwave Smart has no webhook-delete service. |

Lights, switches, thermostats, TRVs, covers, sensors (including power and energy), the button lock, and status LEDs are still created.

## What to delete

Leave Lightwave 2 installed until the new devices are visible and the automations that matter have been retargeted. Then remove it, so each button press is handled by Lightwave Smart.

1. Install Lightwave Smart from the Marketplace or HACS. Installation steps are in the [readme](../README.md#installation).
2. Add it under Settings > Devices & services > Add integration > Lightwave Smart. Choose Refresh Token. The password is not stored. Password Authentication is the other method. At first only the hub may show. Devices appear within a few seconds. Only one Lightwave account can be set up.
3. Open the Lightwave Smart devices and note the new entity ids. Update automations, scripts, scenes, and dashboards. For button presses, use the event entity. The automation editor lists the event types. See [UI Button Events](../README.md#ui-button-events).
4. Delete the Lightwave 2 integration: Settings > Devices & services > Lightwave 2 > three-dot menu > Delete. That removes its devices and entities.
5. Remove the old files. In HACS, uninstall Lightwave 2 and delete the custom repository if you added `https://github.com/bigbadblunt/homeassistant-lightwave2`. If you copied the files yourself, delete `<ha_config_dir>/custom_components/lightwave2`.
6. Restart Home Assistant.

## What you gain

- A maintained integration. Lightwave2's own readme sends you here.
- Refresh Token sign-in, which does not store your password.
- Button presses as event entities. The automation editor lists `Short.1`, `Down.Long`, and the other types. Numeric codes still arrive on the deprecated `lightwave_smart.click` event, in the table above.
- A firmware update entity on generation 2 devices. That entity can install an update.
- A Cloud Link sensor on each hub. Home Assistant raises a repair when the connection is still down two minutes after it drops, and removes the repair when the link connects again.
- Reload on the integration's three-dot menu when you add a device. A restart still works.
- Smart LED strip drivers (L84V24) follow the colour function set in the Lightwave app: White, RGB, RGB with White, or Warm White Cool White.
- A device that Home Assistant is creating for the first time is suggested for the Lightwave room it belongs to. Set the area again for devices you already had under Lightwave2, because those device entries are new.

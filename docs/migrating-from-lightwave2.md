# Moving from Lightwave2

Lightwave Smart replaces the [Lightwave2 integration](https://github.com/bigbadblunt/homeassistant-lightwave2) and its [fork](https://github.com/ikb42/homeassistant-lightwave2), which are archived and no longer maintained.

Lightwave2 uses the domain `lightwave2`. Lightwave Smart uses `lightwave_smart`, and its unique IDs are different, so Home Assistant creates new device and entity registry entries.

**Remove Lightwave 2 before adding Lightwave Smart** if you want to keep clean entity IDs (`switch.lounge_lamp` instead of `switch.lounge_lamp_2`). Most entities generate the same default IDs in both integrations, so freeing the old IDs first lets Home Assistant reuse them and keep existing entity-based dashboards, YAML automations, and Energy dashboard history intact. If Lightwave 2 is still installed when you add Lightwave Smart, every name that is already taken gets a `_2` suffix, and that suffix stays even after you delete Lightwave 2.

The hardware requirement is unchanged. You need a [Link Plus](https://lightwaverf.com/collections/all/products/link-plus) hub. Connect Series (generation 1) and Smart Series (generation 2) devices on that hub are both controlled. A Connect Series hub (Lightwave Link / WiFi Link) does not work.

## Recommended steps (keeps clean entity IDs)

Removing Lightwave 2 first frees its entity IDs so Lightwave Smart can claim the same default names without `_2` suffixes.

1. **Export your current Lightwave 2 entity list (optional, recommended if you customised names or areas).**
   Open **Developer tools > Template**, paste the snippet below, and copy the output somewhere safe for reference:

   ```jinja2
   {% for e in integration_entities('lightwave2') -%}
   {{ e }} | {{ state_attr(e, 'friendly_name') }} | {{ area_name(e) or 'no area' }}
   {% endfor %}
   ```

   Also check **Settings > Devices & services > Lightwave v2 (Smart Series) custom component** (or **Lightwave 2**) for any entities you disabled or renamed away from the Lightwave app name.
2. **Delete the Lightwave 2 integration.**
   Go to **Settings > Devices & services > Lightwave v2 (Smart Series) custom component** (or **Lightwave 2**) **> three-dot menu > Delete**. This removes its devices and entity registry entries so their entity IDs are free again. State history and long-term Energy statistics remain in the Home Assistant database.
3. **Remove the old Lightwave 2 files and restart.**
   - **Home Assistant 2026.11 or newer**: in **Settings > Marketplace**, open **Lightwave RF** (`homeassistant-lightwave2`) and select **Uninstall**.
   - **Home Assistant 2026.10 or older**: in **HACS**, uninstall **Lightwave RF** (`homeassistant-lightwave2`), and delete the custom repository if you added `https://github.com/bigbadblunt/homeassistant-lightwave2` or `https://github.com/ikb42/homeassistant-lightwave2`.
   - **Manual install**: delete `<ha_config_dir>/custom_components/lightwave2`.
   - Restart Home Assistant.
4. **Install and set up Lightwave Smart.**
   - Install **Lightwave Smart** from the Marketplace or HACS. Installation steps are in the [readme](../README.md#installation).
   - Add it under **Settings > Devices & services > Add integration > Lightwave Smart**. Choose **Account Linking** (recommended, signs in directly on Lightwave in your browser without entering or storing your password in Home Assistant; **Refresh Token** and **Password Authentication** are also available). At first only the hub may show; devices appear within a few seconds. Only one Lightwave account can be set up.
5. **Update anything that still changed.**
   - Re-apply any custom entity IDs or custom areas from your saved list, and enable `Signal Strength`, `Voltage`, or hub `Last Event Received` on the device page if you used them (those diagnostic sensors now start disabled).
   - Update Device-based automations, `lightwave2.*` service calls, `lightwave2.click` button automations, and any status LED or binary sensor references listed under [What changes](#what-changes). For button presses, use the device's event entity; see [UI Button Events](../README.md#ui-button-events).

### Alternative: side-by-side migration

If you prefer to leave Lightwave 2 installed while you inspect the new devices and retarget automations one by one, install and add Lightwave Smart first (step 4), point your automations, scripts, scenes, and dashboards at the new `_2` entity IDs, and then delete Lightwave 2, remove its files, and restart (steps 2 and 3).

Be aware of the trade-offs with side-by-side migration:

- Every matching entity gets a `_2` suffix (`light.kitchen_2`), which stays after Lightwave 2 is deleted unless you rename each entity ID by hand.
- State history and Energy dashboard statistics start fresh on the `_2` entity IDs instead of continuing on the original IDs.
- Both integrations stay connected and process button presses until Lightwave 2 is deleted.

## What changes

### Entity IDs and history

When you remove Lightwave 2 before adding Lightwave Smart:

- **Default entity IDs that stay the same**: lights (`light.<name>`), switches and sockets (`switch.<name>`), thermostats and TRVs (`climate.<name>`), covers (`cover.<name>`), button locks (`lock.<name>_lock`), and device sensors (`sensor.<name>_current_consumption`, `sensor.<name>_total_consumption`, battery, illuminance, current, voltage, signal strength). Dashboards, scenes, scripts, YAML automations, and the Energy dashboard keep working on those default IDs and continue their existing history and statistics.
- **Default entity IDs that change**:
  - **Status LEDs**: `light.<name>_led` (one per gang) becomes one shared `light.<primary_gang>_off_led` per device, or `light.<name>_on_led_indicator` and `light.<name>_off_led_indicator` per gang on MK4 dimmers.
  - **Binary sensors**: `binary_sensor.<name>` now includes the sensor type (`binary_sensor.<name>_socket_in_use`, `_window_position`, `_movement`, `_digitalinput`).
  - **Hub sensors**: the hub name now includes the Lightwave structure name (for example `Link Plus Home`), so hub `Dawn Time`, `Dusk Time`, and `Last Event Received` entity IDs include it too.
- **Device automations and customisations**:
  - Automations, scripts, or dashboard cards that reference a **Device** (`device_id`) rather than an **Entity ID** must be pointed at the new Lightwave Smart device.
  - Custom entity IDs or friendly names, disabled entity flags, and Home Assistant areas whose names differ from your Lightwave rooms do not carry over from the old device entries.

### Attributes, services, and events

Read-only `lwrf_*` attributes are still on the entities. For generation 2 lights, brightness can still be set without turning the light on. The service is `lightwave_smart.set_brightness`, and brightness is 0 to 255.

| Lightwave2 | Lightwave Smart |
| --- | --- |
| `lightwave2.set_brightness` | `lightwave_smart.set_brightness` |
| `lightwave2.click` with a numeric code | An event entity on the device. A socket reports `Short.1` to `Short.5`, `Long`, and `Long-Release`. A dimmer or wirefree prefixes those with `Up.` or `Down.` (`Down.Short.2` is down, pressed twice). The same numeric codes are still fired as `lightwave_smart.click`. That event is deprecated and will be removed. |
| `lightwave2.reconnect` | `lightwave_smart.reconnect`. This should no longer be required. Open an [issue](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/issues) if the connection or device states still need a manual kick. |
| `lightwave2.update_states` | `lightwave_smart.update_states`. Same guidance as reconnect. |
| `lightwave2.whdelete` | Delete automations that called this. Lightwave Smart has no webhook-delete service. |

Lights, switches, thermostats, TRVs, covers, sensors (including power and energy), the button lock, and status LEDs are still created.

## What you gain

- A maintained integration. Lightwave2's own readme sends you here.
- Account Linking sign-in on Lightwave in your browser (or Refresh Token sign-in), neither of which stores your password in Home Assistant.
- Button presses as event entities. The automation editor lists `Short.1`, `Down.Long`, and the other types. Numeric codes still arrive on the deprecated `lightwave_smart.click` event, in the table above.
- A firmware update entity on generation 2 devices. That entity can install an update.
- A Cloud Link sensor on each hub. Home Assistant raises a repair when the connection is still down two minutes after it drops, and removes the repair when the link connects again.
- Reload on the integration's three-dot menu when you add a device. A restart still works.
- Smart LED strip drivers (L84V24) follow the colour function set in the Lightwave app: White, RGB, RGB with White, or Warm White Cool White.
- Each new device entry is placed in the Home Assistant area matching the Lightwave room it belongs to. Only areas with custom names in Home Assistant need to be set again.

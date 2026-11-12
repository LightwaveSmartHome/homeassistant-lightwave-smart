# Lightwave Smart

Home Assistant integration for controlling [Lightwave](https://lightwaverf.com) devices that use a Lightwave [Link Plus hub](https://lightwaverf.com/collections/all/products/link-plus).

Controls both Smart Series (generation 2) and Connect Series (generation 1) devices. Does not work with Connect Series hubs (Lightwave Link / WiFi Link).

## Recipes

### Sockets on for a season

Turn Lightwave sockets on at sunset and off at a time you choose, only between two dates. A season from 1 December to 6 January includes those evenings and stops after 6 January. The month and day repeat each year. Turn on "Also on in the morning" to add a second period, from 07:00 to 09:00 unless you change those times.

A wirefree, dimmer or socket can run a Home Assistant scene from a double press with [Double press runs a scene](#double-press-runs-a-scene). The energy sensor on a Smart socket can be added to the Energy dashboard if you want to see what the lights cost.

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FLightwaveSmartHome%2Fhomeassistant-lightwave-smart%2Fblob%2Fmaster%2Fblueprints%2Fautomation%2Flightwave_smart%2Fseasonal_sockets.yaml)

[View blueprint](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/blob/master/blueprints/automation/lightwave_smart/seasonal_sockets.yaml)

Smart sockets, dimmers, and wirefrees on firmware 5 or newer send each press as a button event. The button blueprints below run something when that event arrives. Import one, pick the Smart Switch or Smart Switch Pair for the gang, and pick the press.

A socket double press is `Short.2`. Holding the socket button is `Long`. A dimmer or wirefree puts `Up.` or `Down.` in front of the same name, so holding the down button is `Down.Long`. A single press toggles the device. A double press or a hold can run one of these as well.

### Double press runs a scene

Turn on a scene from a double press. Change the press if you want a different one.

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FLightwaveSmartHome%2Fhomeassistant-lightwave-smart%2Fblob%2Fmaster%2Fblueprints%2Fautomation%2Flightwave_smart%2Fbutton_press_scene.yaml)

[View blueprint](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/blob/master/blueprints/automation/lightwave_smart/button_press_scene.yaml)

### Long press sets a brightness

Set a light to a brightness you choose when the button is held.

If the button is a dimmer button mapped to a gang's output, that gang dims in steps while the button is held. Letting go stops that dimming. The blueprint runs after the hold event arrives and sets the brightness you chose.

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FLightwaveSmartHome%2Fhomeassistant-lightwave-smart%2Fblob%2Fmaster%2Fblueprints%2Fautomation%2Flightwave_smart%2Flong_press_brightness.yaml)

[View blueprint](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/blob/master/blueprints/automation/lightwave_smart/long_press_brightness.yaml)

Event names are listed under [UI Button Events](#ui-button-events).

<!-- ha-marketplace:exclude-start -->
## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=LightwaveSmartHome&repository=homeassistant-lightwave-smart&category=integration)

### Home Assistant 2026.11 or newer

Open Settings > Marketplace, search for "Lightwave Smart", and select Install. Setup opens when installation finishes.

Home Assistant 2026.11 and newer already includes the Marketplace. If you already installed this through HACS, leave it in place. Home Assistant moves it to the Marketplace when it starts after the update, and your devices stay.

If it does not appear in search, select Add from link in the upper-right of the Marketplace and enter `https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart`. Adding from a link needs a GitHub account connected in the Marketplace.

### Home Assistant 2026.10 or older

Install [HACS](https://www.hacs.xyz/), then search HACS for "Lightwave Smart".

It can also be added in HACS as a custom repository. Use the repository URL `https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart` and set the category to Integration:

<img src="https://lightwave-public-files.s3.eu-west-1.amazonaws.com/home-assistant/LightwaveSmartHomehomeassistant-lightwave-smart.png" width="400" alt="HACS custom repository">

### Manual

Copy all files and folders from `custom_components/lightwave_smart` to `<ha_config_dir>/custom_components/lightwave_smart`. You should then have `<ha_config_dir>/custom_components/lightwave_smart/__init__.py`, `<ha_config_dir>/custom_components/lightwave_smart/switch.py`, `<ha_config_dir>/custom_components/lightwave_smart/translations/en.json`, and the rest of the integration files.

The latest version is at https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/releases/latest

If you install this way, check this repository yourself for updates.

<!-- ha-marketplace:exclude-end -->

## Configuration

On Home Assistant 2026.11 or newer, installing from the Marketplace opens setup. If you closed it, or you are on an older Home Assistant, add it yourself:

1. Go to Settings > Devices & services
2. Select Add integration
3. Search for Lightwave Smart
4. Choose Refresh Token. Password Authentication is the other method.
5. Enter your username (email) and password

Refresh Token does not store the password. Password Authentication does. Instance name is optional. If you leave it blank, your username is used.

Entities for all your devices will be created in Home Assistant. Only one Lightwave account can be set up.

At first only the hub may show, because the other entities are created in the background. Go back to the overview and your devices should appear within a few seconds.

The integration options include Hide entities from HomeKit. When that is on, generation 2 switches, lights, and button events are hidden, and so are binary sensors and firmware updates. Thermostats, covers, locks, sensors, and indicator LEDs stay visible. Hidden entities are hidden in Home Assistant, which keeps them out of HomeKit. Reload is on the three-dot menu of the integration, not in those options.

## Updates

The Marketplace and HACS install the latest stable release. Beta versions are pre-releases. To receive those, turn on Pre-release for this repository. In the Marketplace that switch is on this repository's device under Settings > Devices & services > Marketplace. After an update, restart Home Assistant. The Marketplace raises a repair that can restart it for you.

## Usage
Once configured all switches, lights, thermostats, TRVs, blinds/covers, sensors, wirefrees ("Wire-Free Scene Selectors"), energy monitors etc that are configured in your Lightwave app will be added to Home Assistant.

Blinds open, close, and stop. They show as opening or closing while the relay is moving. They do not report a position or whether they are closed.

Smart LED strip drivers (L84V24) are added as lights. The controls follow the function set in the Lightwave app: White is brightness only, RGB and RGB with White use hue and saturation, and Warm White Cool White uses colour temperature (2000K to 9800K). The Function select on the light changes that mode. A custom output assignment is kept when it still matches the new function.

If you add a new device you can reload the integration (Settings > Devices & services > Lightwave Smart > three-dot menu > Reload) or restart Home Assistant to see the new devices, alternatively remove and re-add the integration.

Sensors, including power consumption, are added with the device. Power and energy are shown with the other controls, including on sockets and dimmers. A thermostat or TRV also has a temperature sensor. Humidity, valve level, and call for heat are added when the device reports those features. The button lock is a Lock entity on the device. Status LEDs are lights on the device: one Off LED, shared across the gangs of a multi-gang device, or an On LED Indicator and an Off LED Indicator on each gang of an MK4 dimmer.

Every feature reported by a Lightwave device is also exposed as a read-only attribute named `lwrf_*`, including features that already have their own entity.

For generation 2 lights, the brightness can be set without turning the light on using `lightwave_smart.set_brightness`. Brightness is 0 to 255.

Generation 2 devices have a firmware update entity, and that entity can install an update.

Each hub has a Cloud Link sensor. Home Assistant raises a repair when the connection is still down two minutes after it drops, and removes it when the link connects again. That sensor stays visible when Hide entities from HomeKit is on.

A device that Home Assistant is creating for the first time is suggested for the Lightwave room it belongs to. A device that is already set up keeps its area, including when that area is empty.

## Firmware 5+ 

### UI Button Events

Button presses are event entities, separate from the switch or light turning on.

A socket (for example an L42) is a Smart Switch. Its events are `Short.1` to `Short.5`, `Long`, and `Long-Release`. Pressing the button once is `Short.1`.

A dimmer or wirefree is a Smart Switch Pair. The same events are prefixed with `Up.` or `Down.`. Pressing the down button twice is `Down.Short.2`.

The automation editor offers those event types.

Example of how this can be used in an Entity automation:

A gang of an L42 named "Lounge Xmas", will appear as an entity called "Lounge Xmas Smart Switch", which is then used with the condition that the Event type is "Short.2" (the action can be anything)

<img src="https://lightwave-public-files.s3.eu-west-1.amazonaws.com/home-assistant/LightwaveSmartHomehomeassistant-lightwave-smart-2.png" width="400" alt="UI Button Events">

[Recipes](#recipes) has a blueprint for a double press that runs a scene, and a long press that sets a brightness.

### Deprecated - lightwave_smart.click events

This is legacy - kept for backward compatibility, will be removed in a future release. Please use UI Button Events instead.

Devices generate `lightwave_smart.click` events when the buttons are pressed. The "code" returned is the type of click:

Code|Hex|Meaning
----|----|----
257|101|Up button single press
258|102|Up button double press
259|103|Up button triple press
260|104|Up button quad press
261+||(and so on - I believe up to 20x click is supported)
512|200|Up button press and hold
768|300|Up button release after long press
4353|1101|Down button single press
4354|1102|Down button double press
4355|1103|Down button triple press
4356|1104|Down button quad press
4357+||(and so on)
4608|1200|Down button press and hold
4864|1300|Down button release after long press

For sockets the codes are the "up button" versions.

## Service calls

`lightwave_smart.reset_enabled_status_to_defaults`: Resets all device/entity enabled statuses to defaults

### Deprecated Services

Improved connection and state management means the following services should no longer be required.  If you experience problems with connectivity or device states please open an issue [here](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/issues).

`lightwave_smart.reconnect`: Force a reconnect to the Lightwave backend

`lightwave_smart.update_states`: Force a read of all device states

## Help

If you have questions or comments, please see [discussions](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/discussions).

If you have noticed a bug, please check for an existing open issue and add to that, or open a new [issue](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/issues).

PRs are most welcome. If you see something that could be improved, please consider a pull request, including improvements to the readme, examples, or other guidance.

If you would like to help maintain this project (review pull requests, and keep an eye on discussions and issues), send a direct message or email to any current maintainer, or open a discussion.

If this integration is useful, a [star on the repository](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart) helps others find it.

## Moving from Lightwave2

Lightwave Smart uses a different domain and unique IDs from the [original Lightwave2 integration](https://github.com/bigbadblunt/homeassistant-lightwave2). Remove Lightwave 2 before adding Lightwave Smart if you want to keep clean entity IDs without `_2` suffixes. [Moving from Lightwave2](docs/migrating-from-lightwave2.md) covers the recommended migration order, what changes, and what you gain.

## Thanks
Credit to Bryan Blunt for the original version https://github.com/bigbadblunt/homeassistant-lightwave2

Original credit to Warren Ashcroft for code used as a base https://github.com/washcroft/LightwaveRF-LinkPlus

# Beta - X

There are known issues with the latest beta v3.5.36-Beta, please avoid using it.  A new Beta is being worked on.

If you have questions or comments please see the [discussion](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/discussions/23)

PRs are most welcome, if you see something that could be improved please consider making a pull request, including improvements to the readme, adding examples or other guidance.


# Lightwave Smart

Home Assistant (https://www.home-assistant.io/) component for controlling Lightwave (https://lightwaverf.com) devices with use of a Lightwave [Link Plus hub](https://shop.lightwaverf.com/collections/all/products/link-plus). 

Controls both generation 1 ("Connect Series") and generation 2 ("Smart Series") devices. Does not work with gen1 hub.

*Note: Entities are created differently from the [original version of this integration - Lightwave2](https://github.com/bigbadblunt/homeassistant-lightwave2), in that regard this version is a breaking change* 

## Setup
There are two ways to set up:

#### 1. Using HACS (preferred)

This component is *not yet* available directly through the Home Assistant Community Store HACS (https://hacs.netlify.com/), a pull request is pending merge as of 5 February 2024 to have the integration included with HACS by default.

However using HACS it can be installed via "Custom repositories" using the repository url (https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart), setting Category "Integration" - see:

![image](https://lightwave-public-files.s3.eu-west-1.amazonaws.com/home-assistant/LightwaveSmartHomehomeassistant-lightwave-smart.png)

If you use this method, your component will always update to the latest version. But you'll need to set up HACS first.

#### 2. Manual
Copy all files and folders from custom_components/lightwave_smart to a `<ha_config_dir>/custom_components/lightwave_smart` directory. (i.e. you should have `<ha_config_dir>/custom_components/lightwave_smart/__init__.py`, `<ha_config_dir>/custom_components/lightwave_smart/switch.py`, `<ha_config_dir>/custom_components/lightwave_smart/translations/en.json` etc)

The latest version is at https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/releases/latest

If you use this method then you'll need to keep an eye on this repository to check for updates.

## Configuration
In Home Assistant:

1. Enter configuration menu
2. Select "Integrations"
3. Click the "+" in the bottom right
4. Choose "Lightwave Smart"
5. Enter username and password
6. This should automatically find all your devices (note initially only the hub may show, if you navigate back to the overview all your devices should appear there within a few seconds)

## Usage
Once configured this should then automatically add all switches, lights, thermostats, TRVs, blinds/covers, sensors, wirefrees and energy monitors that are configured in your Lightwave app. If you add a new device you will need to restart Home Assistant, or remove and re-add the integration.

Various sensor entities (including power consumption) and controls for the button lock and status LED are exposed within the corresponding entities.

All other attributes reported by the Lightwave devices are exposed with the names `lwrf_*`. These are all read-only.

For gen2 devices, the brightness can be set without turning the light on using `lightwave_smart.set_brightness`.

### Firmware 5+ 

### UI Button Events

Switches generate events when pressed independently of any other default or mapped behaviour.

For example pressing the down button twice on a dimmer or wirefree will generate the event "Down.Short.2"
Whereas pressing a button once on a socket (eg L42) will generate a "Short.1" event as there is no up/down element to these buttons.

Example of how this can be used in an Entity automation:

A gang of an L42 named "Lounge Xmas", will appear as an entity called "Lounge Xmas Smart Switch", which is then used with the condition that the Event type is "Short.2" (the action can be anything)

![image](https://lightwave-public-files.s3.eu-west-1.amazonaws.com/home-assistant/LightwaveSmartHomehomeassistant-lightwave-smart-2.png)


### lightwave_smart.click events (legacy)

Legacy - kept for backward compatibility

devices generate `lightwave_smart.click` events when the buttons are pressed. The "code" returned is the type of click:

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

### There are further service calls:

`lightwave_smart.reconnect`: Force a reconnect to the Lightwave servers (only for non-public API, has no effect on public API)
`lightwave_smart.whdelete`: Delete a webhook registration (use this if you get "Received message for unregistered webhook" log messages)
`lightwave_smart.update_states`: Force a read of all states of devices

## Thanks
Credit to Bryan Blunt for the original version https://github.com/bigbadblunt/homeassistant-lightwave2

Original credit to Warren Ashcroft for code used as a base https://github.com/washcroft/LightwaveRF-LinkPlus

## Configuration
In Home Assistant:

1. Enter configuration menu
2. Select "Integrations"
3. Click the "+" in the bottom right
4. Choose "Lightwave Smart"
5. Select authentication method
6. Enter username and password

Entities for all your devices will be created in Home Assistant.

Note: initially only the hub may show as the entities are created in the background, if you navigate back to the overview all your devices should appear there within a few seconds.

## Usage
Once configured all switches, lights, thermostats, TRVs, blinds/covers, sensors, wirefrees ("Wire-Free Scene Selectors"), energy monitors etc that are configured in your Lightwave app will be added to Home Assistant.

If you add a new device you can reload the integration (Configuration -> Integrations -> Lightwave Smart -> Options -> Reload) or Restart Home Assistant to see the new devices, alternatively remove and re-add the integration.

Various sensor entities (including power consumption) and controls for the button lock and status LED are exposed within the corresponding entities.

All other attributes reported by the Lightwave devices are exposed with the names `lwrf_*`. These are all read-only.

For gen2 devices, the brightness can be set without turning the light on using `lightwave_smart.set_brightness`.

## Firmware 5+ 

### UI Button Events

Switches generate events when pressed which are independent of any other default or mapped behaviour.

For example pressing the down button twice on a dimmer or wirefree will generate the event "Down.Short.2"
Whereas pressing a button once on a socket (eg L42) will generate a "Short.1" event as there is no up/down element to these buttons.

Example of how this can be used in an Entity automation:

A gang of an L42 named "Lounge Xmas", will appear as an entity called "Lounge Xmas Smart Switch", which is then used with the condition that the Event type is "Short.2" (the action can be anything)

<img src="https://lightwave-public-files.s3.eu-west-1.amazonaws.com/home-assistant/LightwaveSmartHomehomeassistant-lightwave-smart-2.png" width="400" alt="UI Button Events">

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

### Service calls

`lightwave_smart.reset_enabled_status_to_defaults`: Resets all device/entity enabled statuses to defaults

#### Deprecated Services

Improved connection and state management means the following services should no longer be required.  If you experience problems with connectivity or device states please open an issue [here](https://github.com/LightwaveSmartHome/homeassistant-lightwave-smart/issues).

`lightwave_smart.reconnect`: Force a reconnect to the Lightwave backend

`lightwave_smart.update_states`: Force a read of all device states

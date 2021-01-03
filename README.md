# Awesome Lights

This integration shows how you would go ahead and integrate a physical light into Home Assistant.

If you use this integration as a template, make sure you tweak the following places:

 - `manifest.json`: update the requirements to point at your Python library
 - `light.py`: update the code to interact with your library

### Installation

Copy this folder to `<config_dir>/custom_components/example_light/`.

Add the following entry in your `configuration.yaml`:

```yaml
light:
  - platform: lifx_virtual_lights
    name: Name of entity
    target_light: "<mac:address:of:target:light>"
    zone_start: 0
    zone_end: 31
  - platform: lifx_virtual_lights
    name: Name of entity
    target_light: "<mac:address:of:target:light>"
    zone_start: 16
    zone_end: 31
```

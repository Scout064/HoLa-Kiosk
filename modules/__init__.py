"""HoLa Kiosk data-source modules.

Each subdirectory is a standalone module in the HoLa-Dash `DataSource`
pattern (service.py connector + plugin.py presentation). They are loaded by
the HoLa-Dash backend through `plugins.directories` in
config/settings.yaml — nothing in this repo patches the dashboard engine.

Module directory names must be unique across all module roots (the loader
registers them on sys.path by name).
"""

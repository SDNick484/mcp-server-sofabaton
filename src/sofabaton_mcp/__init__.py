"""MCP server for Sofabaton X1/X1S/X2 hubs: over an X2's own MQTT link, or through sofabaton-x-server."""

# The one place the version lives: pyproject.toml reads it from here. Bump it for every release, since pip
# keeps an installed copy whose version matches, even when the code under it changed.
__version__ = "0.2.0"

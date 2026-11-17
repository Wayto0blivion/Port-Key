"""Port-Key: a browser-based SSH connection manager for one person."""

# The version the app reports (health, settings, Diagnostics). The runtime image does not install
# the package, so this cannot be read from pyproject.toml at run time: bump both together
# (tests/unit/skeleton/test_release.py and the release workflow check they match).
__version__ = "0.1.0"

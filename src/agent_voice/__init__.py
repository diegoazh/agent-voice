from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-voice")
except PackageNotFoundError:
    __version__ = "0+unknown"

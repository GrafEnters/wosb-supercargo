"""Суперкарго: a trade helper for World of Sea Battle that reads port prices off the screen."""
__version__ = "1.0.0"

# onnxruntime must be loaded before any winrt module, otherwise importing it later segfaults:
# winrt ships an old msvcp140.dll that onnxruntime cannot live with (build.py drops it from the .exe).
import onnxruntime  # noqa: F401,E402

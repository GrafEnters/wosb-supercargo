# onnxruntime must be loaded before any winrt module, otherwise importing it later segfaults.
import onnxruntime  # noqa: F401

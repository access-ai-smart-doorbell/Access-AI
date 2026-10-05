"""
Camera abstraction - the ONLY place in AccessAI that opens a camera.

Why this exists
---------------
OpenCV's cv2.VideoCapture accepts either:
  * an integer index   -> a local webcam (0 = built-in, 1/2 = USB cams), OR
  * a URL string       -> a network MJPEG stream (e.g. an ESP32-CAM).

Because both go through this one class, moving from your laptop webcam to an
ESP32-CAM later is a ONE-LINE change in config.py (CAMERA_SOURCE) - nothing in
the rest of the codebase changes. Never call cv2.VideoCapture anywhere else.
"""

from typing import Optional, Union

try:
    import cv2
    _HAS_CV2 = True
    _IMPORT_ERR: Optional[Exception] = None
except Exception as e:                                # pragma: no cover
    _HAS_CV2 = False
    _IMPORT_ERR = e
    print(f"[Camera] OpenCV not available: {e}")


class Camera:
    """Thin wrapper around cv2.VideoCapture (webcam index OR MJPEG URL)."""

    def __init__(self, source: Union[int, str] = 0,
                 width: int = 640, height: int = 480):
        self.source = source
        self.width = width
        self.height = height
        self.cap = None

    def open(self) -> "Camera":
        """Open the source. Raises RuntimeError with a helpful message on failure."""
        if not _HAS_CV2:
            raise RuntimeError(
                f"OpenCV (cv2) is not installed, so the camera cannot open: "
                f"{_IMPORT_ERR}"
            )
        self.cap = cv2.VideoCapture(self.source)
        if not self.cap or not self.cap.isOpened():
            raise RuntimeError(
                f"Could not open camera source {self.source!r}. "
                "If this is a webcam index, check the device is connected and "
                "not in use by another app. If it is an ESP32/MJPEG URL, check "
                "the device is powered on and reachable on the network."
            )
        # Request the desired resolution (webcams may ignore/round this).
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        return self

    def read(self):
        """Return the newest BGR frame, or None if a frame couldn't be read."""
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        return frame

    def release(self) -> None:
        """Release the underlying device."""
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:                          # pragma: no cover
                pass
            self.cap = None

    # --- context manager sugar -------------------------------------------
    def __enter__(self) -> "Camera":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

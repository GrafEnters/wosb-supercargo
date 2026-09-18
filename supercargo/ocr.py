"""Windows built-in OCR (Windows.Media.Ocr) wrapper."""
import asyncio
from dataclasses import dataclass

from PIL import Image
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = OcrEngine.try_create_from_language(Language("ru"))
        if _engine is None:
            raise RuntimeError("Russian OCR language is not installed in Windows")
    return _engine


@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class Line:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def y(self) -> float:
        return min(w.y for w in self.words)

    @property
    def x(self) -> float:
        return min(w.x for w in self.words)


def _to_software_bitmap(img: Image.Image) -> SoftwareBitmap:
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()  # BGRA8
    writer = DataWriter()
    writer.write_bytes(data)
    return SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height
    )


async def _recognize(img: Image.Image):
    return await _get_engine().recognize_async(_to_software_bitmap(img))


def recognize(img: Image.Image, scale: float = 1.0) -> list[Line]:
    """Run OCR; coordinates are returned in the original image space."""
    if scale != 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    max_dim = OcrEngine.max_image_dimension
    if max(img.size) > max_dim:
        raise ValueError(f"image too large for OCR ({img.size}, max {max_dim})")
    result = asyncio.run(_recognize(img))
    lines = []
    for ln in result.lines:
        words = []
        for w in ln.words:
            r = w.bounding_rect
            words.append(Word(w.text, r.x / scale, r.y / scale, r.width / scale, r.height / scale))
        lines.append(Line(words))
    return lines

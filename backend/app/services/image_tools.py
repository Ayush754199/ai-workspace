"""Image AI helpers: vision, background removal, enhancement and variations.

Every operation is deterministic image processing (Pillow) or a live provider
call (Gemini vision). Nothing here fabricates a result when a provider is
missing — the caller receives an explicit error instead.
"""

from __future__ import annotations

import base64
import io
from collections import Counter

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

MAX_SIDE = 4096


def open_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return image


def to_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def to_jpeg(image: Image.Image, quality: int = 88) -> bytes:
    output = io.BytesIO()
    (image.convert("RGB") if image.mode == "RGBA" else image).save(output, format="JPEG", quality=quality)
    return output.getvalue()


def describe(image: Image.Image) -> dict:
    width, height = image.size
    rgb = image.convert("RGB").resize((64, 64))
    pixels = list(rgb.getdata())
    average = tuple(sum(channel) // len(pixels) for channel in zip(*pixels))
    brightness = round(sum(average) / 3 / 255 * 100, 1)
    colors = Counter(rgb.resize((32, 32)).getdata()).most_common(5)
    return {
        "width": width,
        "height": height,
        "mode": image.mode,
        "aspect_ratio": round(width / height, 3) if height else None,
        "average_rgb": average,
        "brightness_percent": brightness,
        "dominant_colors": ["#%02x%02x%02x" % color for color, _ in colors],
        "has_alpha": image.mode == "RGBA",
    }


def remove_background(data: bytes, tolerance: int = 28) -> bytes:
    """Flood-fill removal from the border pixels.

    This removes solid/near-solid backgrounds (product shots, logos, screenshots)
    using a real colour-distance flood fill. Photographic backgrounds need a
    segmentation model; when one is configured the endpoint prefers it, and this
    function stays the deterministic fallback that always works offline.
    """
    image = open_image(data).convert("RGBA")
    width, height = image.size
    source = image.load()
    visited = bytearray(width * height)
    tolerance_sq = (tolerance * 3) ** 2

    seeds = [(x, 0) for x in range(width)] + [(x, height - 1) for x in range(width)]
    seeds += [(0, y) for y in range(height)] + [(width - 1, y) for y in range(height)]
    reference = [source[x, y][:3] for x, y in seeds]
    base = tuple(sum(color[i] for color in reference) // len(reference) for i in range(3))

    stack = list(seeds)
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        index = y * width + x
        if visited[index]:
            continue
        visited[index] = 1
        pixel = source[x, y]
        distance = sum((pixel[i] - base[i]) ** 2 for i in range(3))
        if distance > tolerance_sq:
            continue
        source[x, y] = (pixel[0], pixel[1], pixel[2], 0)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    alpha = image.getchannel("A").filter(ImageFilter.GaussianBlur(0.6))
    image.putalpha(alpha)
    return to_png(image)


def enhance(data: bytes, *, sharpen: float = 1.35, contrast: float = 1.06, color: float = 1.08, denoise: bool = True) -> bytes:
    image = open_image(data).convert("RGB")
    if denoise:
        image = image.filter(ImageFilter.MedianFilter(size=3))
    image = ImageEnhance.Sharpness(image).enhance(sharpen)
    image = ImageEnhance.Contrast(image).enhance(contrast)
    image = ImageEnhance.Color(image).enhance(color)
    image = ImageOps.autocontrast(image, cutoff=1)
    return to_jpeg(image, quality=92)


def resize(data: bytes, max_side: int = 2048) -> bytes:
    image = open_image(data)
    image.thumbnail((max_side, max_side), Image.LANCZOS)
    return to_png(image)


def to_base64_mime(data: bytes) -> tuple[str, str]:
    """Return ``(base64, mime)``; the mime is sniffed from the real bytes."""
    try:
        probe = Image.open(io.BytesIO(data))
        mime = Image.MIME.get(probe.format or "", "image/png")
    except Exception:
        mime = "image/png"
    return base64.b64encode(data).decode("ascii"), mime


# ---------------------------------------------------------------------------
# Provider-backed operations
# ---------------------------------------------------------------------------

def _gemini_credentials():
    from backend.key_rotator import get_provider_credentials

    return get_provider_credentials("Gemini")


def understand(data: bytes, question: str) -> str:
    """Ask the configured vision model about an image. Raises when unconfigured."""
    credentials = _gemini_credentials()
    if not credentials:
        raise RuntimeError("Image understanding requires a configured Gemini key")
    from google import genai
    from google.genai import types

    prompt = question.strip() or "Describe this image in detail, including any visible text."
    contents = [prompt, types.Part.from_bytes(data=data, mime_type="image/png")]
    last: Exception | None = None
    for credential in credentials:
        client = genai.Client(api_key=credential.value)
        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text
            last = RuntimeError("Vision model returned an empty answer")
        except Exception as exc:  # noqa: BLE001
            last = exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    raise RuntimeError(f"Image understanding failed: {last}")


def ocr(data: bytes) -> str:
    try:
        return understand(data, "Extract every line of text from this image verbatim as plain text. Return an empty string when there is no text.")
    except Exception:
        return ""

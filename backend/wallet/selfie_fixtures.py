"""Synthetic selfies and a stand-in face detector for tests.

The real detector (YuNet) needs a real face, and the repository holds no
photos of people. Tests draw a head-and-shoulders picture instead and replace
``selfie._detect`` with ``detector(...)``, which returns landmarks described
in frame shares. A colour tag in the top-left corner tells the stand-in which
face each picture shows, so straight and turned frames can differ.
"""
from __future__ import annotations

import base64
import io
import math
import random
from contextlib import contextmanager
from functools import lru_cache
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFilter

from . import selfie

#: A frontal face in the middle of a 3:4 frame, as the oval guide asks for.
FRONTAL = {"box": (0.28, 0.26, 0.44, 0.40), "yaw": 0.5, "roll": 0.0, "pitch": 0.55, "score": 0.95}
_TAG = 16  # pixels


def portrait(width=720, height=960, *, tag=0, light=1.0, scene="room", blur=0.0,
             face_light=None, seed=0) -> Image.Image:
    """A textured head-and-shoulders picture. ``light`` scales everything,
    ``face_light`` only the face (backlit/flash), ``scene``/``seed`` pick the
    background, ``blur`` softens it all. Sensor noise differs per ``tag``, as
    it does between frames of one camera."""
    rng = random.Random(1000 + seed)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    if scene == "room":
        for y in range(0, height, 8):
            shade = 150 - y * 40 // height
            draw.rectangle([0, y, width, y + 8], fill=(shade, shade - 12, shade - 30))
        for _ in range(40):
            x, y = rng.randrange(width), rng.randrange(height // 2)
            draw.rectangle([x, y, x + rng.randrange(30, 90), y + rng.randrange(20, 60)],
                           fill=(rng.randrange(60, 200), rng.randrange(60, 160), rng.randrange(40, 120)))
    elif scene == "bright":
        draw.rectangle([0, 0, width, height], fill=(250, 250, 248))
    else:  # "street": another place entirely
        for x in range(0, width, 12):
            draw.rectangle([x, 0, x + 12, height], fill=(30 + x * 50 // width, 90, 170))
        for _ in range(40):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.ellipse([x, y, x + 50, y + 50], fill=(220, 220, rng.randrange(0, 80)))
    # Shoulders (someone else, elsewhere, wears something else) and a head.
    draw.ellipse([width * 0.08, height * 0.72, width * 0.92, height * 1.3],
                 fill=(40, 52, 90) if scene != "street" else (210, 70, 40))
    left, top = width * 0.26, height * 0.2
    right, bottom = width * 0.74, height * 0.72
    draw.ellipse([left, top, right, bottom], fill=(150, 104, 80))
    face_w, face_h = right - left, bottom - top
    for side in (0.32, 0.68):  # brows, eyes, pupils
        cx = left + face_w * side
        draw.rectangle([cx - face_w * 0.1, top + face_h * 0.3, cx + face_w * 0.1, top + face_h * 0.33],
                       fill=(40, 25, 20))
        draw.ellipse([cx - face_w * 0.08, top + face_h * 0.38, cx + face_w * 0.08, top + face_h * 0.45],
                     fill=(235, 235, 230))
        draw.ellipse([cx - face_w * 0.03, top + face_h * 0.39, cx + face_w * 0.03, top + face_h * 0.44],
                     fill=(30, 20, 15))
    draw.polygon([(left + face_w * 0.5, top + face_h * 0.45), (left + face_w * 0.44, top + face_h * 0.62),
                  (left + face_w * 0.56, top + face_h * 0.62)], fill=(120, 80, 60))
    draw.ellipse([left + face_w * 0.36, top + face_h * 0.72, left + face_w * 0.64, top + face_h * 0.8],
                 fill=(120, 40, 45))
    # Skin and sensor texture, so focus can be measured.
    import numpy as np

    noise = np.random.default_rng(1000 + tag).integers(-14, 15, size=(height, width, 1))
    image = Image.fromarray(np.clip(np.asarray(image, dtype=np.int16) + noise, 0, 255).astype(np.uint8))
    if face_light is not None:
        mask = Image.new("L", (width, height))
        ImageDraw.Draw(mask).ellipse([left, top, right, bottom], fill=255)
        image.paste(image.point(lambda value: max(0, min(255, int(value * face_light)))), mask=mask)
    if light != 1.0:
        image = image.point(lambda value: max(0, min(255, int(value * light))))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    ImageDraw.Draw(image).rectangle([0, 0, _TAG - 1, _TAG - 1], fill=(20 + 40 * tag, 200, 20))
    return image


def jpeg(image: Image.Image, quality: int = 90, **save) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, **save)
    return buffer.getvalue()


def b64(image: Image.Image, quality: int = 90, **save) -> str:
    return base64.b64encode(jpeg(image, quality, **save)).decode("ascii")


@lru_cache(maxsize=64)
def _cached_selfie(items) -> str:
    return b64(portrait(**dict(items)))


def selfie_b64(**kwargs) -> str:
    """A selfie that passes every check while ``detector()`` is active."""
    return _cached_selfie(tuple(sorted(kwargs.items())))


def face(width, height, *, box=FRONTAL["box"], yaw=0.5, roll=0.0, pitch=0.55,
         score=0.95) -> selfie.Face:
    """A detected face in a ``width`` x ``height`` image, from frame shares."""
    x, y, w, h = box[0] * width, box[1] * height, box[2] * width, box[3] * height
    centre = (x + w / 2, y + h / 2)
    eye_y, mouth_y = y + 0.38 * h, y + 0.78 * h
    left, right = (x + 0.3 * w, eye_y), (x + 0.7 * w, eye_y)
    nose = (left[0] + yaw * (right[0] - left[0]), eye_y + pitch * (mouth_y - eye_y))
    mouth = (x + 0.5 * w, mouth_y)
    angle = math.radians(roll)

    def turn(point):
        dx, dy = point[0] - centre[0], point[1] - centre[1]
        return (centre[0] + dx * math.cos(angle) - dy * math.sin(angle),
                centre[1] + dx * math.sin(angle) + dy * math.cos(angle))

    return selfie.Face(x, y, w, h, turn(left), turn(right), turn(nose), turn(mouth), score,
                       width, height)


def _tag_of(image: Image.Image) -> int | None:
    red, green, blue = image.convert("RGB").getpixel((2, 2))
    tag = (red - 20) / 40
    if not (170 <= green <= 230 and blue <= 50 and -0.25 <= tag - round(tag) <= 0.25 and tag > -0.25):
        return None
    return max(0, round(tag))


@contextmanager
def detector(faces=None, *, upright_only=False):
    """Stand in for YuNet. ``faces`` maps a picture's tag to a list of face
    specs (keyword arguments of ``face``); a tagged picture not in ``faces``
    shows one frontal face. A picture without its tag in place shows none:
    that is how a contrast-stretched or wrongly turned copy looks, so the
    upright original is the only one where a face is found. ``upright_only``
    also finds no face in landscape frames, like a real detector given a
    photo the camera saved sideways."""
    faces = faces if faces is not None else {}

    def detect(rgb):
        width, height = rgb.size
        if upright_only and width > height:
            return []
        tag = _tag_of(rgb)
        if tag is None:
            return []
        specs = faces.get(tag, [{}])
        found = [face(width, height, **spec) for spec in specs]
        found.sort(key=lambda item: item.w * item.h, reverse=True)
        return found

    with patch.object(selfie, "_detect", side_effect=detect), \
            patch.object(selfie, "face_checks_available", return_value=True):
        yield

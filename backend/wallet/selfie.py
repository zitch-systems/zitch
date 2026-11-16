"""Check and prepare the Tier 2 live face photo before it goes to the bank.

Wema's partner-account-upgrade-tier2 compares ``liveImageOfFace`` with the
photo behind the customer's BVN/NIN and decides the upgrade. Nothing here
decides identity. A photo the bank cannot use spends the customer's attempt
and comes back as a bare refusal, so each photo is checked here first and
refused with ONE plain instruction when it is clearly unusable:

* a readable JPEG/PNG/WebP within size bounds, turned upright (EXIF
  orientation, or the orientation a face is found in for sideways captures);
* exactly one face, large enough, fully in the photo, facing the camera;
* the face lit well enough, not washed out or against a bright window, and
  not blurred.

The live face check adds frames taken while the customer turns their head.
``verify_head_turn`` measures where the nose tip sits between the eyes in the
straight and turned frames. A real head turn moves the nose across the eye
line (it stands out from the face); a printed or on-screen photo turned in
front of the camera is flat, so the nose keeps its place between the eyes.
The turned frames must also show the same framing and background as the
straight one.

Limits are deliberately lenient: they reject only photos a face matcher cannot
use. Anything else goes to the bank, which stays the decision boundary.

The photo sent on carries no EXIF/GPS/device metadata and is at most
``BANK_LONG_SIDE`` pixels on its long side. Nothing is stored or logged except
numeric measurements.

Face landmarks come from YuNet (``face_model/``, MIT) through OpenCV. If the
detector cannot load, photos still get the readability, size and light checks
on the centre of the frame, and the head turn cannot be measured.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
import math
import re
import threading
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Input and output bounds
# --------------------------------------------------------------------------- #
#: Inside the 4 MiB API body (API_MAX_BODY_BYTES) with room for the IDs.
MAX_BASE64_CHARS = 3_900_000
MIN_IMAGE_BYTES = 4_000
MAX_PIXELS = 40_000_000          # JPEG: decoded at 1/2-1/8 scale (draft)
#: PNG/WebP decode every pixel (3 bytes each): a small, highly compressible
#: upload must not cost hundreds of MB on a 512 MB instance. Our own cameras
#: send JPEG; this only bounds other uploads.
MAX_PIXELS_FULL_DECODE = 12_000_000
MIN_SHORT_SIDE = 320
MIN_LONG_SIDE = 400
#: Head-turn frames are only measured here, never sent to the bank, so they need
#: only enough pixels to find a face. The page scales them to 480 px on the long
#: side; a 16:9 camera makes that 270x480, under the bank photo's 320 px minimum,
#: and on 16 Nov 2026 every turn frame from such a phone was refused unseen.
TURN_MIN_SHORT_SIDE = 160
TURN_MIN_LONG_SIDE = 200
MAX_ASPECT = 2.4
#: What the bank receives: enough for a face match, small enough to upload fast.
BANK_LONG_SIDE = 1280
BANK_JPEG_QUALITY = 92
#: YuNet is trained on faces of about 10-300 px; a selfie face fills most of
#: the frame, so landmarks are measured on a 400 px copy.
ANALYSIS_LONG_SIDE = 400
_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}

# --------------------------------------------------------------------------- #
# Thresholds. Lenient on purpose: only photos a face matcher cannot use fail.
# Calibrated on frontal portraits and their degraded copies (see
# docs/tier2-live-face-check.md).
# --------------------------------------------------------------------------- #
FACE_SCORE = 0.6            # detector confidence that this is a face
OTHER_FACE_SCORE = 0.75     # a second face this confident ...
OTHER_FACE_RATIO = 0.45     # ... and at least this wide (vs ours) is a second person
MIN_FACE_WIDTH = 0.16       # face box width, share of the photo width
MIN_EYE_DISTANCE = 36       # pixels between the eyes in the photo sent to the bank
CUT_MARGIN = 0.12           # the face box may overhang the photo by this share of it
MAX_ROLL = 25.0             # degrees the eye line may tilt
MAX_YAW_OFFSET = 0.25       # nose position between the eyes, 0.5 when facing the camera
PITCH_RANGE = (0.25, 0.9)   # nose depth between eye line and mouth
DARK_FACE = 40              # mean face brightness, 0-255
BRIGHT_FACE = 228
CLIPPED_FACE = 0.35         # share of face pixels burnt out to white
BACKLIGHT_GAP = 110         # background this much brighter than the face ...
BACKLIGHT_FACE = 60         # ... while the face is this dark (a lit face is fine on any wall)
BLANK_STD = 7.0             # an even field: covered lens, wall, ceiling
MAX_BLUR = 0.75             # perceptual blur of the face, 0 sharp - 1 flat (Crete et al.)
MIN_TURN = 0.15             # nose shift along the eye line for a head turn
SCENE_CORR = 0.2            # turned frames are another scene only when the background
SCENE_HIST = 0.6            # neither lines up (correlation) nor shares its colours
SCENE_FLAT_STD = 6.0        # a plain wall cannot tell two scenes apart
UPRIGHT_SCORE = 0.9         # below this, sideways orientations are also tried

# --------------------------------------------------------------------------- #
# What the customer is told. One instruction each; never bank or vendor text.
# --------------------------------------------------------------------------- #
MESSAGES = {
    "selfie_missing": "Take a live selfie with your front camera to continue.",
    "selfie_unreadable": "We couldn't read that photo. Please take it again.",
    "selfie_too_large": "That photo is too large. Please take it again with the camera on this page.",
    "selfie_too_small": "That photo is too small. Please take it again with your front camera.",
    "selfie_no_face": ("We couldn't see your face. Hold the phone at eye level with your face "
                       "inside the oval, in good light."),
    "selfie_many_faces": "We can see more than one face. Make sure only you are in the photo.",
    "selfie_face_small": "Your face is too far away. Bring the phone closer so your face fills the oval.",
    "selfie_face_cut": ("Part of your face is outside the photo. Hold the phone a little further "
                        "away so your whole face fits in the oval."),
    "selfie_not_facing": "Look straight at the camera with your head level, then try again.",
    "selfie_too_dark": "Your face is too dark. Move to a brighter place or face a window or lamp.",
    "selfie_too_bright": ("There is too much light on your face. Move out of direct sunlight or "
                          "away from the lamp."),
    "selfie_backlit": "There is bright light behind you. Turn around so the light is in front of you.",
    "selfie_blurry": "The photo is blurry. Hold the phone still and try again.",
    "liveness_missing": "Finish the head-turn step of the face check.",
    "liveness_no_face": ("We lost your face while you turned. Turn your head only a little, "
                         "keeping your face in the oval."),
    "liveness_no_turn": ("We didn't see your head turn. Look at the camera, then slowly turn your "
                         "head to one side when asked."),
    "liveness_mismatch": "Keep the phone still and stay in the oval for the whole check, then try again.",
}
#: How far through the checks a photo got; the most useful refusal among
#: several frames is the one that got furthest.
_STAGES = ("selfie_missing", "selfie_too_large", "selfie_unreadable", "selfie_too_small",
           "selfie_no_face", "selfie_many_faces", "selfie_face_cut", "selfie_face_small",
           "selfie_too_dark", "selfie_backlit", "selfie_too_bright", "selfie_not_facing",
           "selfie_blurry", "ok")


def message(code: str) -> str:
    return MESSAGES.get(code, MESSAGES["selfie_unreadable"])


# --------------------------------------------------------------------------- #
# Face detector (YuNet through OpenCV), loaded once, shared by request threads
# --------------------------------------------------------------------------- #
_MODEL = Path(__file__).resolve().parent / "face_model" / "face_detection_yunet_2023mar.onnx"
_MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
_lock = threading.Lock()
_detector = None
_detector_failed = False


def _get_detector():
    """The shared detector, or None when OpenCV or the model is unavailable."""
    global _detector, _detector_failed
    if _detector is not None or _detector_failed:
        return _detector
    with _lock:
        if _detector is None and not _detector_failed:
            try:
                import cv2
                import numpy as np

                data = _MODEL.read_bytes()
                if hashlib.sha256(data).hexdigest() != _MODEL_SHA256:
                    raise ValueError("face model checksum mismatch")
                # One request thread at a time runs the detector (under _lock);
                # a single OpenCV thread keeps a small instance responsive.
                cv2.setNumThreads(1)
                _detector = cv2.FaceDetectorYN.create(
                    "onnx", np.frombuffer(data, np.uint8), np.array([], np.uint8),
                    (ANALYSIS_LONG_SIDE, ANALYSIS_LONG_SIDE), 0.5, 0.3, 50)
            except Exception:  # noqa: BLE001 - a missing wheel must not take Tier 2 down
                _detector_failed = True
                log.warning("selfie_face_detector_unavailable", exc_info=True)
    return _detector


def face_checks_available() -> bool:
    return _get_detector() is not None


@dataclass(frozen=True)
class Face:
    """One detected face in analysis-image pixels."""
    x: float
    y: float
    w: float
    h: float
    left_eye: tuple[float, float]    # the eye on the image's left
    right_eye: tuple[float, float]
    nose: tuple[float, float]
    mouth: tuple[float, float]       # midpoint of the mouth corners
    score: float
    frame_w: int
    frame_h: int

    @property
    def eye_distance(self) -> float:
        return math.dist(self.left_eye, self.right_eye)

    @property
    def roll(self) -> float:
        """Tilt of the eye line in degrees; 0 for a level head."""
        return math.degrees(math.atan2(self.right_eye[1] - self.left_eye[1],
                                       self.right_eye[0] - self.left_eye[0]))

    @property
    def yaw(self) -> float:
        """Where the nose tip sits along the eye line: 0.5 facing the camera,
        towards 0 or 1 as the head turns. Unchanged by tilt and distance."""
        ex, ey = self.right_eye[0] - self.left_eye[0], self.right_eye[1] - self.left_eye[1]
        span = ex * ex + ey * ey
        if not span:
            return 0.5
        nx, ny = self.nose[0] - self.left_eye[0], self.nose[1] - self.left_eye[1]
        return (nx * ex + ny * ey) / span

    @property
    def pitch(self) -> float:
        """How far the nose tip sits from the eye line towards the mouth (0-1)."""
        ex, ey = self.right_eye[0] - self.left_eye[0], self.right_eye[1] - self.left_eye[1]
        span = math.hypot(ex, ey)
        if not span:
            return 0.5

        def depth(point):
            return ((point[0] - self.left_eye[0]) * -ey + (point[1] - self.left_eye[1]) * ex) / span

        mouth = depth(self.mouth)
        return depth(self.nose) / mouth if mouth > 0 else 0.0

    @property
    def centre(self) -> tuple[float, float]:
        """Box centre as a share of the frame."""
        return (self.x + self.w / 2) / self.frame_w, (self.y + self.h / 2) / self.frame_h

    @property
    def width_share(self) -> float:
        return self.w / self.frame_w


def _detect(rgb: Image.Image):
    """Faces in ``rgb``, largest first; None when no detector is available."""
    detector = _get_detector()
    if detector is None:
        return None
    import numpy as np

    pixels = np.ascontiguousarray(np.asarray(rgb.convert("RGB"))[:, :, ::-1])
    height, width = pixels.shape[:2]
    with _lock:
        detector.setInputSize((width, height))
        _, rows = detector.detect(pixels)
    faces = []
    for row in rows if rows is not None else ():
        values = [float(v) for v in row[:15]]
        if not all(math.isfinite(v) for v in values):
            continue
        points = [(values[4 + 2 * i], values[5 + 2 * i]) for i in range(5)]
        left, right = sorted(points[:2])
        mouth = ((points[3][0] + points[4][0]) / 2, (points[3][1] + points[4][1]) / 2)
        faces.append(Face(values[0], values[1], values[2], values[3], left, right,
                          points[2], mouth, values[14], width, height))
    faces.sort(key=lambda face: face.w * face.h, reverse=True)
    return faces


# --------------------------------------------------------------------------- #
# Assessment
# --------------------------------------------------------------------------- #
@dataclass
class Selfie:
    code: str = "ok"
    face: Face | None = None
    metrics: dict = field(default_factory=dict)
    #: SHA-256 of the decoded upload; a liveness proof is bound to it.
    digest: str = ""
    image: Image.Image | None = field(default=None, repr=False)      # upright RGB, bank size
    analysis: Image.Image | None = field(default=None, repr=False)   # upright greyscale, 400 px
    _original: bytes = field(default=b"", repr=False)  # forwarded as-is when already clean

    @property
    def ok(self) -> bool:
        return self.code == "ok"

    @property
    def message(self) -> str:
        return "" if self.ok else message(self.code)

    @property
    def score(self) -> float:
        """Rank between passing frames: sharp, evenly lit, facing the camera."""
        m = self.metrics
        sharp = min(max((0.8 - m.get("blur", 0.8)) / 0.5, 0.0), 1.0)
        light = 1.0 - min(abs(m.get("face_mean", 135.0) - 135.0) / 135.0, 1.0)
        pose = 1.0 - min(abs(m.get("yaw", 0.5) - 0.5) / 0.3 + abs(m.get("roll", 0.0)) / 60.0, 1.0)
        return round(0.45 * sharp + 0.3 * light + 0.25 * pose, 4)

    def bank_bytes(self) -> bytes:
        """The JPEG the bank receives: the upload itself when it is already a
        clean upright JPEG within bounds, otherwise one re-encode without any
        metadata."""
        if self._original:
            return self._original
        buffer = io.BytesIO()
        self.image.save(buffer, "JPEG", quality=BANK_JPEG_QUALITY, optimize=True)
        return buffer.getvalue()

    def bank_base64(self) -> str:
        return base64.b64encode(self.bank_bytes()).decode("ascii")


_DATA_URL = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64")
_SPACE = re.compile(r"\s+")


def decode(value) -> tuple[bytes, str]:
    """``(bytes, "")`` for a base64 image (plain or data URL), else ``(b"", code)``."""
    if not isinstance(value, str) or not value.strip():
        return b"", "selfie_missing"
    if len(value) > MAX_BASE64_CHARS:
        return b"", "selfie_too_large"
    value = value.strip()
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or not _DATA_URL.fullmatch(header.strip()):
            return b"", "selfie_unreadable"
    # Some Android encoders wrap lines every 76 characters.
    value = _SPACE.sub("", value)
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return b"", "selfie_unreadable"
    if len(raw) < MIN_IMAGE_BYTES:
        return b"", "selfie_unreadable"
    return raw, ""


def _fit(image: Image.Image, long_side: int) -> Image.Image:
    width, height = image.size
    scale = long_side / max(width, height)
    if scale >= 1:
        return image
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)


def _open(raw: bytes, *, min_short: int = MIN_SHORT_SIDE, min_long: int = MIN_LONG_SIDE):
    """``(upright RGB image, clean)`` or ``(None, code)``. ``clean`` means the
    upload is a JPEG with no metadata that needed no turning or resizing."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
            if image.format not in _FORMATS:
                return None, "selfie_unreadable"
            limit = MAX_PIXELS if image.format in ("JPEG", "MPO") else MAX_PIXELS_FULL_DECODE
            if image.width * image.height > limit:
                return None, "selfie_too_large"
            clean = (image.format == "JPEG" and image.mode == "RGB"
                     and all(marker == "APP0" for marker, _ in getattr(image, "applist", []))
                     and max(image.size) <= BANK_LONG_SIDE)
            if image.format in ("JPEG", "MPO"):
                # Decode large camera JPEGs at 1/2-1/8 scale (never below the
                # bank size) instead of decoding every pixel.
                image.draft("RGB", (BANK_LONG_SIDE, BANK_LONG_SIDE))
            image.load()
            if not clean:
                # A clean JPEG carries no EXIF, so no orientation to apply.
                image = ImageOps.exif_transpose(image) or image
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, TypeError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        return None, "selfie_unreadable"
    width, height = image.size
    if min(width, height) < min_short or max(width, height) < min_long:
        return None, "selfie_too_small"
    if max(width, height) / min(width, height) > MAX_ASPECT:
        return None, "selfie_unreadable"
    if max(width, height) > BANK_LONG_SIDE:
        clean = False
        image = _fit(image, BANK_LONG_SIDE)
    return image, clean


def _array(gray: Image.Image):
    import numpy as np

    return np.asarray(gray, dtype=np.float32)


def _region(pixels, box, frame):
    """Pixels inside ``box`` (x0, y0, x1, y1 in frame shares or pixels), clipped."""
    height, width = pixels.shape[:2]
    x0, y0, x1, y1 = box
    if frame:
        x0, x1, y0, y1 = x0 * width, x1 * width, y0 * height, y1 * height
    x0, x1 = max(0, int(x0)), min(width, int(math.ceil(x1)))
    y0, y1 = max(0, int(y0)), min(height, int(math.ceil(y1)))
    return pixels[y0:y1, x0:x1]


def _blur(image: Image.Image, face: Face) -> float:
    """Perceptual blur of the face: 0 for crisp detail, towards 1 for a smear.

    No-reference metric of Crete et al. (2007): blur the face again and see how
    much of its pixel-to-pixel variation survives. A sharp face loses most of
    it, an already blurred one hardly changes. It is a ratio, so brightness and
    skin tone do not move it the way plain edge energy does. Measured at a
    fixed 192 px face width so the limit holds for every camera resolution.
    """
    import numpy as np

    scale = image.width / face.frame_w
    margin = 0.08 * face.w
    box = (max(0, (face.x + margin) * scale), max(0, (face.y + margin) * scale),
           min(image.width, (face.x + face.w - margin) * scale),
           min(image.height, (face.y + face.h - margin) * scale))
    if box[2] - box[0] < 16 or box[3] - box[1] < 16:
        return 1.0
    crop = image.crop(tuple(int(v) for v in box)).convert("L")
    if crop.width > 192:
        crop = crop.resize((192, max(16, round(192 * crop.height / crop.width))),
                           Image.Resampling.LANCZOS)
    pixels = _array(crop)
    blur = 0.0
    for axis in (0, 1):
        # 9-pixel moving average along one axis, edges repeated.
        pad = [(4, 4) if a == axis else (0, 0) for a in range(2)]
        total = np.cumsum(np.pad(pixels, pad, mode="edge"), axis=axis, dtype=np.float64)
        zero = np.zeros_like(np.take(total, [0], axis=axis))
        total = np.concatenate([zero, total], axis=axis)
        size = total.shape[axis]
        smooth = (np.take(total, range(9, size), axis=axis)
                  - np.take(total, range(0, size - 9), axis=axis)) / 9.0
        detail = np.abs(np.diff(pixels, axis=axis))
        kept = np.abs(np.diff(smooth, axis=axis))
        variation = float(detail.sum())
        if variation <= 0:
            return 1.0
        blur = max(blur, (variation - float(np.maximum(0.0, detail - kept).sum())) / variation)
    return blur


def _light(pixels, face: Face) -> dict:
    """Face and background brightness from the analysis greyscale."""
    import numpy as np

    inner = _region(pixels, (face.x + 0.2 * face.w, face.y + 0.3 * face.h,
                             face.x + 0.8 * face.w, face.y + 0.9 * face.h), frame=False)
    if inner.size == 0:
        return {"face_mean": 0.0, "face_std": 0.0, "clipped": 0.0}
    height, width = pixels.shape
    rows, cols = np.mgrid[0:height, 0:width]
    head = ((cols >= face.x - 0.3 * face.w) & (cols <= face.x + 1.3 * face.w)
            & (rows >= face.y - 0.45 * face.h))
    around = (rows <= face.y + face.h) & ~head
    result = {"face_mean": float(inner.mean()), "face_std": float(inner.std()),
              "clipped": float((inner >= 250).mean())}
    if around.sum() >= 0.05 * pixels.size:
        result["surround_mean"] = float(pixels[around].mean())
    return result


def _best_score(faces) -> float:
    return max((face.score for face in faces), default=0.0)


def _find_faces(small: Image.Image):
    """``(faces, transpose)``: the faces, and the turn that made the photo
    upright when the camera saved it sideways. ``faces`` is None when no
    detector is available.

    The detector still finds a sideways face, but less confidently and with
    landmarks in the wrong places, so a weak upright result is compared with
    the other three orientations and the clearly better one wins."""
    faces = _detect(small)
    if faces is None:
        return None, None
    if _best_score(faces) < FACE_SCORE:
        # A dim photo: stretch its contrast before deciding there is no face.
        stretched = _detect(ImageOps.autocontrast(small, cutoff=1))
        if _best_score(stretched) > _best_score(faces):
            faces = stretched
    best, best_turn = faces, None
    if _best_score(faces) < UPRIGHT_SCORE:
        for turn in (Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_270,
                     Image.Transpose.ROTATE_180):
            turned = _detect(small.transpose(turn))
            if (_best_score(turned) >= FACE_SCORE
                    and _best_score(turned) >= _best_score(best) + 0.1):
                best, best_turn = turned, turn
    return best, best_turn


def _check_without_detector(selfie: Selfie, pixels) -> Selfie:
    """Without landmarks: light and empty-frame checks on the centre of the frame."""
    centre = _region(pixels, (0.3, 0.25, 0.7, 0.75), frame=True)
    mean, std = float(centre.mean()), float(centre.std())
    selfie.metrics.update(face_mean=round(mean, 1), face_std=round(std, 1))
    if mean < DARK_FACE:
        selfie.code = "selfie_too_dark"
    elif std < BLANK_STD:
        selfie.code = "selfie_no_face"
    elif mean > BRIGHT_FACE:
        selfie.code = "selfie_too_bright"
    return selfie


def assess(value, *, frontal: bool = True) -> Selfie:
    """Check one photo. ``frontal`` photos must suit the bank; head-turn
    frames (``frontal=False``) only need to show one face."""
    raw, code = decode(value)
    if code:
        return Selfie(code)
    selfie = Selfie(digest=hashlib.sha256(raw).hexdigest())
    image, clean = (_open(raw) if frontal
                    else _open(raw, min_short=TURN_MIN_SHORT_SIDE, min_long=TURN_MIN_LONG_SIDE))
    if image is None:
        selfie.code = clean
        return selfie
    small = _fit(image, ANALYSIS_LONG_SIDE)
    faces, transpose = _find_faces(small)
    if transpose is not None:
        image, small, clean = image.transpose(transpose), small.transpose(transpose), False
    selfie.image, selfie.analysis = image, small.convert("L")
    selfie._original = raw if clean else b""
    pixels = _array(selfie.analysis)
    selfie.metrics.update(width=image.width, height=image.height,
                          rotated=transpose is not None)
    if faces is None:
        return _check_without_detector(selfie, pixels)
    found = [face for face in faces if face.score >= FACE_SCORE]
    if not found:
        centre = _region(pixels, (0.3, 0.25, 0.7, 0.75), frame=True)
        selfie.metrics.update(face_mean=round(float(centre.mean()), 1))
        selfie.code = "selfie_too_dark" if float(centre.mean()) < DARK_FACE else "selfie_no_face"
        return selfie
    face = found[0]
    selfie.face = face
    selfie.metrics.update(
        faces=len(found), face_score=round(face.score, 3), face_width=round(face.width_share, 3),
        eye_distance=round(face.eye_distance * image.width / face.frame_w, 1),
        yaw=round(face.yaw, 3), roll=round(face.roll, 1), pitch=round(face.pitch, 3))
    if any(other.score >= OTHER_FACE_SCORE and other.w >= OTHER_FACE_RATIO * face.w
           for other in faces[1:]):
        selfie.code = "selfie_many_faces"
        return selfie
    if not frontal:
        return selfie
    overhang_x, overhang_y = CUT_MARGIN * face.w, CUT_MARGIN * face.h
    if (face.x < -overhang_x or face.y < -overhang_y
            or face.x + face.w > face.frame_w + overhang_x
            or face.y + face.h > face.frame_h + overhang_y):
        selfie.code = "selfie_face_cut"
        return selfie
    if (face.width_share < MIN_FACE_WIDTH
            or selfie.metrics["eye_distance"] < MIN_EYE_DISTANCE):
        selfie.code = "selfie_face_small"
        return selfie
    light = _light(pixels, face)
    selfie.metrics.update({key: round(value, 3) for key, value in light.items()})
    behind = light.get("surround_mean", 0.0) - light["face_mean"] > BACKLIGHT_GAP
    if light["face_mean"] < BACKLIGHT_FACE and behind:
        # The camera exposed for a window or lamp behind the customer. Turning
        # round fixes it; "too dark" would send them looking for more light.
        selfie.code = "selfie_backlit"
    elif light["face_mean"] < DARK_FACE:
        selfie.code = "selfie_too_dark"
    elif light["face_mean"] > BRIGHT_FACE or light["clipped"] > CLIPPED_FACE:
        selfie.code = "selfie_too_bright"
    elif (abs(face.roll) > MAX_ROLL or abs(face.yaw - 0.5) > MAX_YAW_OFFSET
          or not PITCH_RANGE[0] <= face.pitch <= PITCH_RANGE[1]):
        selfie.code = "selfie_not_facing"
    if not selfie.ok:
        return selfie
    selfie.metrics["blur"] = round(_blur(image, face), 3)
    if selfie.metrics["blur"] > MAX_BLUR:
        selfie.code = "selfie_blurry"
    return selfie


def pick_frontal(values) -> tuple[int, Selfie]:
    """The best passing photo among a short burst, or the most useful refusal."""
    checked = [assess(value) for value in values]
    passing = [(selfie.score, -index, index) for index, selfie in enumerate(checked) if selfie.ok]
    if passing:
        _, _, index = max(passing)
        return index, checked[index]
    if not checked:
        return -1, Selfie("selfie_missing")
    counts = Counter(selfie.code for selfie in checked)
    index = max(range(len(checked)), key=lambda i: (
        counts[checked[i].code], _STAGES.index(checked[i].code) if checked[i].code in _STAGES else 0))
    return index, checked[index]


# --------------------------------------------------------------------------- #
# Head turn
# --------------------------------------------------------------------------- #
def _head_mask(face: Face, width: int, height: int):
    """The head and hair around a face, scaled to a ``width`` x ``height`` grid."""
    import numpy as np

    scale_x, scale_y = width / face.frame_w, height / face.frame_h
    rows, cols = np.mgrid[0:height, 0:width]
    return ((cols >= (face.x - 0.25 * face.w) * scale_x) & (cols <= (face.x + 1.25 * face.w) * scale_x)
            & (rows >= (face.y - 0.5 * face.h) * scale_y) & (rows <= (face.y + 1.15 * face.h) * scale_y))


def _same_scene(first: Selfie, second: Selfie) -> bool:
    """False only when everything around the head is clearly another place:
    the background neither lines up with the straight photo nor shares its
    colours. A turned head, a hand that moved the phone a little or the camera
    re-exposing still pass; a straight photo taken somewhere else does not."""
    import numpy as np

    width = 48
    height = max(8, round(width * first.image.height / first.image.width))
    keep = ~(_head_mask(first.face, width, height) | _head_mask(second.face, width, height))
    if keep.sum() < 0.15 * keep.size:
        return True  # the head fills the frame: nothing to compare
    colour_a = np.asarray(first.image.resize((width, height), Image.Resampling.BOX))[keep]
    colour_b = np.asarray(second.image.resize((width, height), Image.Resampling.BOX))[keep]
    grey_a, grey_b = colour_a.mean(axis=1), colour_b.mean(axis=1)
    if grey_a.std() < SCENE_FLAT_STD or grey_b.std() < SCENE_FLAT_STD:
        return True  # a plain wall: no evidence either way
    correlation = float(np.corrcoef(grey_a, grey_b)[0, 1])

    def histogram(colour):
        bins = (colour // 64).astype(int)
        counts = np.bincount(bins[:, 0] * 16 + bins[:, 1] * 4 + bins[:, 2], minlength=64)
        return counts / max(counts.sum(), 1)

    overlap = float(np.minimum(histogram(colour_a), histogram(colour_b)).sum())
    return not (correlation < SCENE_CORR and overlap < SCENE_HIST)


def _consistent(frontal: Selfie, turned: Selfie) -> bool:
    first, second = frontal.face, turned.face
    aspect_a = frontal.image.width / frontal.image.height
    aspect_b = turned.image.width / turned.image.height
    if abs(aspect_a - aspect_b) > 0.08:
        return False
    if not 0.6 <= second.width_share / first.width_share <= 1.7:
        return False
    (ax, ay), (bx, by) = first.centre, second.centre
    if math.hypot(ax - bx, ay - by) > 0.75 * first.width_share:
        return False
    return _same_scene(frontal, turned)


def verify_head_turn(frontal: Selfie, frames) -> tuple[str, dict]:
    """``("ok", metrics)`` when the frames show this face turning; else a
    ``liveness_*`` code. ``frontal`` is the passing straight photo."""
    if not frontal.ok or frontal.face is None:
        return "liveness_missing", {}
    frames = list(frames or ())
    if not frames:
        return "liveness_missing", {}
    seen = matched = 0
    turn = 0.0
    refused = Counter()
    for value in frames:
        turned = assess(value, frontal=False)
        if turned.code == "selfie_many_faces":
            return "liveness_mismatch", {"frames": len(frames), "faces": seen}
        if not turned.ok or turned.face is None:
            # Why each frame went unseen, so a refusal like 16 Nov 2026's (every
            # frame too small, reported only as "no face") shows in the log.
            refused[turned.code if not turned.ok else "selfie_no_face"] += 1
            continue
        seen += 1
        if not _consistent(frontal, turned):
            continue
        matched += 1
        turn = max(turn, abs(turned.face.yaw - frontal.face.yaw))
    metrics = {"frames": len(frames), "faces": seen, "matched": matched, "turn": round(turn, 3)}
    if refused:
        metrics["frame_codes"] = ",".join(f"{code}:{n}" for code, n in sorted(refused.items()))
    if not seen:
        return "liveness_no_face", metrics
    if not matched:
        return "liveness_mismatch", metrics
    if turn < MIN_TURN:
        return "liveness_no_turn", metrics
    return "ok", metrics

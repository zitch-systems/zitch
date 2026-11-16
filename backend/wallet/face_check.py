"""Tier 2 live face check, shared by the app and the WhatsApp face page.

The customer's camera sends a short burst of straight photos and a few frames
taken while they turn their head. ``check_live_face`` keeps the clearest
straight photo that the bank can use (``selfie.pick_frontal``) and confirms the
turn came from a real, three-dimensional face in front of the camera
(``selfie.verify_head_turn``). Nothing goes to the bank and nothing is stored.

The app checks before the customer submits Tier 2 and receives a proof: a
signed, 15-minute token bound to the customer, their current credentials and
the SHA-256 of that exact photo. ``upgrade_wema_identity`` accepts the photo
with its proof. The WhatsApp page checks and submits in one request, so it
passes the checked photo straight through.
"""
from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.core import signing
from django.utils.crypto import salted_hmac

from . import selfie

log = logging.getLogger(__name__)

PROOF_SALT = "wallet.tier2-face-proof.v1"
PROOF_TTL_SECONDS = 15 * 60
MAX_FRONTAL_FRAMES = 3
MAX_TURN_FRAMES = 8
UNAVAILABLE = ("Face checks are temporarily unavailable. Your details are saved; "
               "please try again shortly.")
EXPIRED = "Your face check has expired. Please take it again."
UPDATE_REQUIRED = ("Update the Zitch app to finish Tier 2. The face check now includes "
                   "a short head turn.")


@dataclass
class FaceCheck:
    code: str
    frontal: selfie.Selfie | None = None
    index: int = -1
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code == "ok"

    @property
    def message(self) -> str:
        if self.code == "face_check_unavailable":
            return UNAVAILABLE
        return "" if self.ok else selfie.message(self.code)


def frame_list(value, limit: int) -> list[str] | None:
    """A list of 1..``limit`` base64 strings, or None."""
    if (not isinstance(value, list) or not 1 <= len(value) <= limit
            or not all(isinstance(item, str) and item for item in value)):
        return None
    return value


def check_live_face(frames, turns) -> FaceCheck:
    """The clearest straight photo plus a measured head turn, or a refusal."""
    index, frontal = selfie.pick_frontal(frames)
    if not frontal.ok:
        return FaceCheck(frontal.code, frontal, index, dict(frontal.metrics))
    if not selfie.face_checks_available():
        # The turn cannot be measured without landmarks: refuse rather than
        # pass a check that did not run.
        log.error("tier2_face_check_unavailable reason=no_face_detector")
        return FaceCheck("face_check_unavailable", frontal, index)
    code, metrics = selfie.verify_head_turn(frontal, turns)
    return FaceCheck(code, frontal, index, {**frontal.metrics, **metrics})


def _stamp(user) -> str:
    # Password and PIN are stored hashes; changing either (or the phone)
    # retires every outstanding proof.
    value = json.dumps([user.pk, user.phone, user.password, user.transaction_pin])
    return salted_hmac(PROOF_SALT + ".credentials", value, algorithm="sha256").hexdigest()


def issue_proof(user, frontal: selfie.Selfie) -> str:
    return signing.dumps({"u": user.pk, "d": frontal.digest, "c": _stamp(user)},
                         salt=PROOF_SALT, compress=False)


def proof_matches(user, token, frontal: selfie.Selfie) -> bool:
    if not isinstance(token, str) or not token or len(token) > 512 or not frontal.digest:
        return False
    try:
        claims = signing.loads(token, salt=PROOF_SALT, max_age=PROOF_TTL_SECONDS)
    except (signing.BadSignature, ValueError, TypeError):
        return False
    return (isinstance(claims, dict) and claims.get("u") == user.pk
            and hmac.compare_digest(str(claims.get("d", "")), frontal.digest)
            and hmac.compare_digest(str(claims.get("c", "")), _stamp(user)))


def liveness_required() -> bool:
    return bool(getattr(settings, "TIER2_LIVENESS_REQUIRED", False))


def log_outcome(channel: str, user, result: FaceCheck) -> None:
    """Numbers only: never images, identities or provider payloads."""
    keys = ("faces", "face_width", "eye_distance", "yaw", "roll", "face_mean", "blur",
            "frames", "matched", "turn", "frame_codes", "rotated")
    measured = " ".join(f"{key}={result.metrics[key]}" for key in keys if key in result.metrics)
    log.info("tier2_face_check channel=%s user=%s code=%s frame=%s %s",
             channel, user.pk, result.code, result.index, measured)
    try:
        _watch_failures(channel, user.pk, result.code)
    except Exception:  # noqa: BLE001 - watching must never fail a face check
        log.warning("tier2_face_watch_failed", exc_info=True)


#: A customer who fails this many checks in an hour is stuck; one failure reason
#: from this many different customers in an hour is a fault on our side. On
#: 16 Nov 2026 every Android customer with a 16:9 camera failed with
#: liveness_no_face and nothing told anyone; either rule would have.
STUCK_FAILURES = 3
SPREAD_CUSTOMERS = 3
_WATCH_SECONDS = 3600


def _watch_failures(channel: str, user_pk, code: str) -> None:
    if code == "ok":
        return
    from django.core.cache import cache

    from utility.alerts import alert

    def bump(key):
        cache.add(key, 0, _WATCH_SECONDS)
        try:
            return cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.set(key, 1, _WATCH_SECONDS)
            return 1

    if bump(f"tier2_face:user:{user_pk}") >= STUCK_FAILURES and cache.add(
            f"tier2_face:stuck-alerted:{user_pk}", 1, 6 * 3600):
        alert("Tier 2 face check: a customer has failed 3 times in an hour and may be stuck",
              level="error", user_id=user_pk, channel=channel, last_code=code)
    if cache.add(f"tier2_face:seen:{code}:{user_pk}", 1, _WATCH_SECONDS):
        customers = bump(f"tier2_face:code:{code}")
        if customers >= SPREAD_CUSTOMERS and cache.add(f"tier2_face:spread-alerted:{code}", 1, 3 * 3600):
            alert("Tier 2 face check: the same failure for several customers in an hour - "
                  "check the face page and wallet/selfie.py", level="error",
                  code=code, customers=customers, channel=channel)

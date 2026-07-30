"""Device-aware risk scoring.

The client stamps `X-Zitch-Device` (a per-install id kept in the keychain) and
`X-Zitch-Device-Info` (coarse hardware facts plus root/emulator indication) on every
API call — see `lib/deviceIntegrity.ts`. This module turns those into a score, and
applies exactly one new money gate off the back of it.

Three principles, in order of importance:

1. **The signals are evidence, never a verdict.** Everything here is
   attacker-controlled: a compromised device reports whatever it likes, and an
   attacker who reads this file can flip `rooted=1` to `rooted=0`. So a clean signal
   earns no trust — it merely fails to raise suspicion. The controls that actually
   stop a drained account are the ones an attacker cannot influence: the transaction
   PIN, the tier caps, the velocity brake, and the ledger.

2. **It steps up; it does not silently block.** A refusal a customer cannot act on is
   indistinguishable from an outage, and a fraud control that produces those gets
   turned off. The one gate added here reuses the existing face-verification step-up,
   which has a clear message and a path forward.

3. **An unreadable signal is not a clean signal.** A missing device header scores as
   unknown, not as safe — otherwise stripping one header would be the cheapest way to
   look trustworthy.
"""
import logging

log = logging.getLogger("zitch.security")

DEVICE_HEADER = "X-Zitch-Device"
DEVICE_INFO_HEADER = "X-Zitch-Device-Info"

# Score contributions. Deliberately coarse — these are ordinal hints for a human
# reading an alert, not a calibrated probability, and pretending otherwise would
# invite tuning them as though they were.
SCORE_UNKNOWN_DEVICE = 30      # never seen this install for this user
SCORE_NO_DEVICE_HEADER = 20    # an older build, a third-party client, or a stripped header
SCORE_ROOTED = 35
SCORE_EMULATOR = 25
SCORE_ROOT_UNKNOWN = 5         # the check could not run — mildly interesting
SCORE_NEW_IP = 10              # different IP from the last call on this device

HIGH_RISK = 50                 # at or above: alert an operator


def parse_device(request) -> dict:
    """The device facts from a request's headers. Always returns a dict; `id` is ""
    when the client sent no device header at all."""
    raw_id = (request.headers.get(DEVICE_HEADER) or "").strip()[:64]
    info = (request.headers.get(DEVICE_INFO_HEADER) or "").strip()[:256]
    parsed = {}
    for part in info.split(";"):
        key, _, value = part.partition("=")
        if key.strip():
            parsed[key.strip()] = value.strip()
    return {
        "id": raw_id,
        "os": parsed.get("os", "")[:16],
        "os_version": parsed.get("osv", "")[:24],
        "model": parsed.get("model", "")[:48],
        "brand": parsed.get("brand", "")[:32],
        # `physical=0` means an emulator. Absent means the client didn't say.
        "emulator": parsed.get("physical") == "0",
        # Tri-state on purpose: True / False / None for "could not check".
        "rooted": {"1": True, "0": False}.get(parsed.get("rooted"), None),
    }


def attach_device(request, user) -> dict:
    """Parse the device off `request` and hang it on `user`.

    Threaded via the user object rather than a new parameter on every money function,
    or a thread-local: `require_user` is the one place every authenticated request
    passes through with both objects in hand, so the attribute is set at the
    authentication boundary and read wherever the user is already available.
    """
    device = parse_device(request)
    device["ip"] = _client_ip(request)
    user.zitch_device = device
    return device


def device_for(user) -> dict:
    """The device attached to this user by `attach_device`, or an empty dict for a
    non-HTTP caller (a cron, the WhatsApp router) where there is no request."""
    return getattr(user, "zitch_device", None) or {}


def _client_ip(request) -> str:
    from common.ratelimit import client_ip
    return client_ip(request)


def score_device(user, device: dict) -> tuple:
    """(score, reasons) for this device acting for this user. Read-only."""
    from accounts.models import KnownDevice

    reasons = []
    score = 0
    if not device.get("id"):
        return SCORE_NO_DEVICE_HEADER, ["no_device_header"]

    known = KnownDevice.objects.filter(user=user, device_id=device["id"]).first()
    if known is None:
        score += SCORE_UNKNOWN_DEVICE
        reasons.append("unknown_device")
    elif device.get("ip") and known.last_ip and device["ip"] != known.last_ip:
        # Weak on its own — mobile IPs change constantly — so it is scored low and
        # only matters stacked with something else.
        score += SCORE_NEW_IP
        reasons.append("new_ip")

    if device.get("rooted") is True:
        score += SCORE_ROOTED
        reasons.append("rooted")
    elif device.get("rooted") is None:
        score += SCORE_ROOT_UNKNOWN
        reasons.append("root_check_unavailable")

    if device.get("emulator"):
        score += SCORE_EMULATOR
        reasons.append("emulator")

    return score, reasons


def remember_device(user, device: dict) -> None:
    """Record this device against the user. Never raises.

    Only writes when something actually changed, because this sits on the path of
    every authenticated request and an unconditional UPDATE per call would be a write
    per API call for no benefit.
    """
    from django.utils import timezone

    from accounts.models import KnownDevice

    if not device.get("id"):
        return
    try:
        known, created = KnownDevice.objects.get_or_create(
            user=user, device_id=device["id"],
            defaults={"os": device.get("os", ""), "model": device.get("model", ""),
                      "brand": device.get("brand", ""), "last_ip": device.get("ip", "")},
        )
        if created:
            log.info("device_first_seen user=%s device=%s model=%r ip=%s",
                     user.pk, device["id"][:12], device.get("model"), device.get("ip"))
            return
        changed = {}
        if device.get("ip") and device["ip"] != known.last_ip:
            changed["last_ip"] = device["ip"]
        # `last_seen` is refreshed at most hourly: it exists so an operator can tell a
        # dormant device from an active one, which does not need per-request accuracy.
        if (timezone.now() - known.last_seen).total_seconds() > 3600:
            changed["last_seen"] = timezone.now()
        if changed:
            for field, value in changed.items():
                setattr(known, field, value)
            known.save(update_fields=[*changed, "updated"] if hasattr(known, "updated")
                       else list(changed))
    except Exception:  # noqa: BLE001 — risk telemetry must never break a request
        log.exception("remember_device_failed user=%s", getattr(user, "pk", "?"))


def evaluate_login(request, user) -> tuple:
    """Score a successful sign-in, record the device, and alert when it looks wrong.

    Deliberately does NOT change the login contract: a high score does not refuse the
    sign-in or demand a second factor. The password was correct, and turning a
    heuristic into a login block would lock out real customers who bought a new phone.
    What it buys is that a takeover leaves a durable, queryable trail from the first
    minute rather than being reconstructed afterwards from rotated logs.
    """
    device = attach_device(request, user)
    score, reasons = score_device(user, device)
    remember_device(user, device)

    if score >= HIGH_RISK:
        from utility.alerts import alert
        alert("High-risk sign-in", level="warning", user_id=user.pk, score=score,
              reasons=reasons, device=device.get("id", "")[:12], ip=device.get("ip", ""))
        try:
            from whatsapp.ops import record_audit
            record_audit("auth.high_risk_signin", actor_type="system", target=f"u_{user.pk}",
                         after={"score": score, "reasons": reasons,
                                "device": device.get("id", "")[:12], "ip": device.get("ip", "")})
        except Exception:  # noqa: BLE001
            log.exception("high_risk_signin_audit_failed user=%s", user.pk)
    else:
        log.info("signin_risk user=%s score=%s reasons=%s", user.pk, score, reasons)
    return score, reasons


def new_device_step_up_error(user, amount) -> "str | None":
    """The one money gate this module adds: a large spend from a device we have never
    seen for this user needs face verification first.

    This is the shape of the fraud it addresses. A stolen password (or a session
    lifted from a backup) arrives on a device the account has never used, and the
    first thing it does is move as much as the tier allows. The tier cap alone doesn't
    help — the attacker stays under it. Face verification does, because it needs the
    customer, not their credentials.

    Returns None (allowing the spend) whenever:
      * the amount is under the threshold — everyday spending on a new phone must not
        require a selfie;
      * the user is already face-verified — the step-up has nothing left to ask;
      * the device is known, or the client sent no device header at all. The second is
        a deliberate hole: gating on a missing header would break older builds and
        the WhatsApp channel, which have no device to report. Closing it means
        requiring the header, which is a client-rollout decision, not a code one.
    """
    from django.conf import settings

    from accounts.models import KnownDevice

    threshold = getattr(settings, "RISK_NEW_DEVICE_FACE_THRESHOLD", None)
    if not threshold or threshold <= 0:
        return None
    device = device_for(user)
    if not device.get("id"):
        return None
    if user.face_verified:
        return None
    if amount < threshold:
        return None
    if KnownDevice.objects.filter(user=user, device_id=device["id"]).exists():
        return None
    log.warning("new_device_step_up user=%s device=%s amount=%s",
                user.pk, device["id"][:12], amount)
    return ("For your security, verify your face before sending this much from a new "
            "device.")

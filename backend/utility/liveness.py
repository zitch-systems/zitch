"""Tier-2 authorization requires a provider-attested interactive session.

Prembly's image Face Liveliness API assesses the supplied image; it does not
attest that the authenticated customer completed a live camera challenge.
https://docs.prembly.com/docs/face-livelinness-check

Enable this capability only after implementing and validating a secret-authenticated
provider session readback that binds the subject, server-issued action/challenge,
terminal result, provider reference and bank-submitted image to this customer.
Client callbacks, uploaded photographs, scores, an SDK public key, or a configured
environment flag cannot establish that proof. There is currently no such adapter.
"""

TIER2_LIVENESS_UNAVAILABLE = (
    "Live face verification is temporarily unavailable. "
    "Your current verification and account access have not changed. "
    "Please contact Zitch Support for help."
)


def tier2_face_available() -> bool:
    """No production or simulation switch can stand in for a session adapter."""
    return False


def verify_tier2_liveness(user, data) -> dict:
    """Fail closed until a server-verified, customer-bound SDK session exists.

    Deliberately ignore image data and client claims. Downstream bank-contract
    tests may isolate this dependency explicitly; runtime never fabricates proof.
    """
    return {
        "success": False,
        "unavailable": True,
        "code": "tier2_liveness_unavailable",
        "message": TIER2_LIVENESS_UNAVAILABLE,
    }

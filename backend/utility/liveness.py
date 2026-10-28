"""Retired liveness entry points. 9PSB identity verification owns face checks."""
TIER2_LIVENESS_UNAVAILABLE = "Use 9PSB face verification on the secure verification page."
def tier2_face_available():
    return False

def _unavailable(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": TIER2_LIVENESS_UNAVAILABLE}
start_tier2_liveness = _unavailable
verify_tier2_liveness = _unavailable
refresh_tier2_liveness = _unavailable

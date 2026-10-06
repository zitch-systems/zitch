"""Redacted operator reports; this module never authorizes bank traffic."""


def readiness_report(stage="validation"):
    from .management.commands.vas_preflight import build_report

    try:
        return build_report(stage=stage)
    except Exception:  # A broken inspection is not readiness or public error detail.
        return {
            "stage": stage,
            "read_only": True,
            "local_ready": False,
            "full_go_live_ready": False,
            "status": "inspection_unavailable",
            "checks": [{"code": "inspection_completed", "status": "fail"}],
            "scope": "Inspection could not complete; bank acceptance and settlement remain unverified.",
        }

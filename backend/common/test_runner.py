"""Django test runner that makes accidental provider egress impossible.

The application's provider clients all use ``requests``.  A developer can have
live credentials in a local ``.env`` (and CI can inject production-like
configuration), so relying on every test to remember to blank every key is not a
safe boundary.  Tests that intentionally exercise an HTTP client already mock
the module-level ``requests.get/post/...`` call.  Anything else is a missing mock
and must fail before a credential, OTP, or fixture payload leaves the process.
"""
from unittest.mock import patch

from django.test.runner import DiscoverRunner


def _blocked_request(*_args, **_kwargs):
    raise AssertionError(
        "Outbound HTTP is disabled in tests; mock the provider request explicitly"
    )


class NoNetworkDiscoverRunner(DiscoverRunner):
    """Run the suite with the lowest-level ``requests`` send path blocked."""

    def run_tests(self, test_labels, **kwargs):
        with patch("requests.sessions.Session.request", side_effect=_blocked_request):
            return super().run_tests(test_labels, **kwargs)

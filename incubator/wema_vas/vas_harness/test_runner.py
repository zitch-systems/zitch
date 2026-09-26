from unittest.mock import patch

from django.test.runner import DiscoverRunner


def denied(*args, **kwargs):
    raise AssertionError("Network connections are prohibited in VAS tests")


class NoNetworkRunner(DiscoverRunner):
    def run_tests(self, test_labels, **kwargs):
        with patch("socket.socket.connect", side_effect=denied), patch("socket.create_connection", side_effect=denied):
            return super().run_tests(test_labels, **kwargs)

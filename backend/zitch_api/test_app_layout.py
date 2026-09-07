"""Project-level invariant: no app's tests can go missing silently.

`compliance/__init__.py` was absent when that app was added. Python 3 namespace packages
let Django load the app anyway — `manage.py check` passed, migrations ran, the endpoints
worked, and `manage.py test compliance` ran all 33 tests. But `manage.py test` with no
argument uses unittest discovery, which does not traverse a namespace package, so those
33 tests were skipped by CI while everything looked green.

That is the worst failure mode a test suite can have: coverage that reports success
because it never ran. Cheap to prevent, invisible otherwise.
"""
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import TestCase

BACKEND = Path(settings.BASE_DIR)


def _local_apps():
    """Apps whose code lives in this repository (not site-packages)."""
    for config in apps.get_app_configs():
        path = Path(config.path)
        try:
            path.relative_to(BACKEND)
        except ValueError:
            continue   # third-party (django.contrib, corsheaders, …)
        yield config


class AppLayoutTests(TestCase):
    def test_every_local_app_is_a_real_package(self):
        missing = [c.label for c in _local_apps() if not (Path(c.path) / "__init__.py").exists()]
        self.assertEqual(
            missing, [],
            f"These apps have no __init__.py: {missing}. Django loads them as namespace "
            "packages, so everything appears to work — but `manage.py test` (unittest "
            "discovery) silently skips their tests.")

    def test_every_test_module_lives_in_a_package_discovery_can_reach(self):
        # Same failure one level down: a tests module inside a subdirectory that is not a
        # package is equally invisible.
        offenders = []
        for config in _local_apps():
            root = Path(config.path)
            for test_file in root.rglob("test*.py"):
                for parent in test_file.parents:
                    if parent == root.parent:
                        break
                    if not (parent / "__init__.py").exists():
                        offenders.append(str(test_file.relative_to(BACKEND)))
                        break
        self.assertEqual(offenders, [],
                         f"Test modules unreachable by discovery: {offenders}")

    def test_discovery_actually_finds_every_apps_tests(self):
        # The direct assertion, rather than only its precondition: load the suite the way
        # `manage.py test` does and check each app that HAS a tests module is represented.
        import unittest

        loader = unittest.TestLoader()
        suite = loader.discover(str(BACKEND), pattern="test*.py", top_level_dir=str(BACKEND))
        self.assertEqual(loader.errors, [], f"discovery errors: {loader.errors}")

        found = set()

        def walk(node):
            for item in node:
                if isinstance(item, unittest.TestSuite):
                    walk(item)
                else:
                    found.add(type(item).__module__.split(".")[0])

        walk(suite)

        expected = {
            config.label for config in _local_apps()
            if any(Path(config.path).glob("test*.py"))
        }
        self.assertEqual(
            expected - found, set(),
            f"These apps have tests that discovery does not reach: {sorted(expected - found)}")

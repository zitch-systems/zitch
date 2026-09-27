#!/usr/bin/env python
"""Independent, synthetic-only VAS development entry point."""
import os
import sys

if os.environ.get("DJANGO_SETTINGS_MODULE", "vas_harness.settings") != "vas_harness.settings":
    raise SystemExit("VAS harness cannot use the existing application's settings.")
if any(arg == "--settings" or arg.startswith("--settings=") for arg in sys.argv):
    raise SystemExit("VAS harness settings cannot be overridden.")
os.environ["DJANGO_SETTINGS_MODULE"] = "vas_harness.settings"

from django.core.management import execute_from_command_line

if __name__ == "__main__":
    execute_from_command_line(sys.argv)

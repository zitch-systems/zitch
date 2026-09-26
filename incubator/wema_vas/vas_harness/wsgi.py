"""Independent WSGI entry point; never imports the existing Zitch backend."""
import os

from django.core.wsgi import get_wsgi_application

if os.environ.get("DJANGO_SETTINGS_MODULE", "vas_harness.settings") != "vas_harness.settings":
    raise RuntimeError("The VAS service must use only its isolated settings")
os.environ["DJANGO_SETTINGS_MODULE"] = "vas_harness.settings"
application = get_wsgi_application()

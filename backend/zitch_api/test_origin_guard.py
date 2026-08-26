from django.test import Client, TestCase, override_settings


@override_settings(
    ALLOWED_HOSTS=["api.zitch.ng", ".onrender.com"],
    BLOCK_RENDER_ORIGIN=True,
    SECURE_SSL_REDIRECT=False,
)
class RenderOriginGuardTests(TestCase):
    def test_direct_origin_only_exposes_health_endpoints(self):
        client = Client(HTTP_HOST="zitch-api-zxdx.onrender.com")
        self.assertEqual(client.get("/healthz").status_code, 200)
        self.assertEqual(client.get("/readyz").status_code, 200)
        self.assertEqual(client.get("/portal/").status_code, 404)
        self.assertEqual(client.get("/admin/").status_code, 404)

    def test_custom_domain_still_serves_application_routes(self):
        client = Client(HTTP_HOST="api.zitch.ng")
        self.assertEqual(client.get("/portal/").status_code, 200)

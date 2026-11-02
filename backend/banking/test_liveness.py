"""The face-check link: live camera frames, a moving face, single use."""
import base64
import json
from io import BytesIO

from django.test import TestCase, override_settings
from PIL import Image, ImageDraw

from .test_client import CONFIG
from .upgrades import stored_documents


def frame(shift=0):
    image = Image.new("RGB", (320, 320), "#d9b38c")
    draw = ImageDraw.Draw(image)
    draw.ellipse((100 + shift, 80, 220 + shift, 240), fill="#8a5a3c")  # a "face" that moves between poses
    out = BytesIO()
    image.save(out, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


@override_settings(NINEPSB=CONFIG, ZITCH_LINKS={"API_BASE": "https://testserver"})
class LivenessTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        from .test_fixtures import make_bank_user
        cache.clear()
        self.user, self.token = make_bank_user("08077770002", "face@zitch.test")

    def link(self):
        response = self.client.post("/api/banking/liveness/start/", data=json.dumps({"access_token": self.token}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        url = response.json()["url"]
        self.assertTrue(url.startswith("https://testserver/verify/face/"))
        return url.removeprefix("https://testserver")

    def submit(self, path, frames):
        return self.client.post(path + "/submit", data=json.dumps({"frames": frames}), content_type="application/json")

    def test_a_moving_face_is_accepted_and_kept_for_the_upgrade(self):
        path = self.link()
        page = self.client.get(path)
        self.assertContains(page, "Start face check")
        self.assertIn("camera=(self)", page["Permissions-Policy"])
        response = self.submit(path, {"straight": frame(0), "left": frame(-70), "right": frame(70)})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("userPhoto", stored_documents(self.user))
        # Single use: the same link cannot be used again.
        self.assertEqual(self.submit(path, {"straight": frame(0), "left": frame(-70), "right": frame(70)}).status_code, 410)

    def test_a_still_photo_is_refused(self):
        path = self.link()
        response = self.submit(path, {"straight": frame(0), "left": frame(0), "right": frame(0)})
        self.assertEqual(response.status_code, 400)
        self.assertIn("turn your head", response.json()["message"])
        self.assertNotIn("userPhoto", stored_documents(self.user))

    def test_a_tampered_or_replaced_link_does_not_work(self):
        old = self.link()
        self.link()  # A new link retires the old one.
        self.assertEqual(self.client.get(old).status_code, 410)
        self.assertEqual(self.client.get(old[:-3] + "abc").status_code, 410)

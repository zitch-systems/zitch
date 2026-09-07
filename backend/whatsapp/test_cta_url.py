"""The CTA-URL sender.

Used for the one link Zitch sends a customer: the bank's face check. Two properties
matter and neither is cosmetic — the URL carries the customer's own BVN in its query
string, and a bare https:// link in a bank's chat is indistinguishable from the
phishing messages we tell people to ignore.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from whatsapp.providers import send_cta_url

LIVE = {"BASE_URL": "https://graph.example/v26.0", "PHONE_NUMBER_ID": "1",
        "TOKEN": "t", "VERIFY_TOKEN": "v", "MODE": "live", "APP_SECRET": "s"}


@override_settings(WHATSAPP=LIVE)
class CtaUrlTests(SimpleTestCase):
    def _payload(self, post):
        return post.call_args.kwargs["json"]

    def test_it_sends_an_interactive_cta_url_not_a_text_link(self):
        with patch("whatsapp.providers.requests.post") as post:
            post.return_value.ok = True
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"messages": [{"id": "wamid.1"}]}
            send_cta_url("234801", "Body", "https://face.example/?bvn=222", cta="Start")
        body = self._payload(post)
        self.assertEqual(body["interactive"]["type"], "cta_url")
        params = body["interactive"]["action"]["parameters"]
        self.assertEqual(params["url"], "https://face.example/?bvn=222")
        self.assertEqual(params["display_text"], "Start")

    def test_the_url_is_not_duplicated_into_the_body(self):
        # The whole point: the target is hidden behind a label.
        with patch("whatsapp.providers.requests.post") as post:
            post.return_value.ok = True
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"messages": [{"id": "wamid.1"}]}
            send_cta_url("234801", "Tap to verify", "https://face.example/?bvn=222")
        self.assertNotIn("face.example", self._payload(post)["interactive"]["body"]["text"])

    def test_a_rejected_interactive_type_falls_back_to_a_tappable_link(self):
        # Older Cloud API versions refuse cta_url. A verification step that silently
        # never arrives is worse than one that arrives as plain text.
        with patch("whatsapp.providers._send_payload",
                   return_value={"success": False, "message": "unsupported"}), \
             patch("whatsapp.providers.send_text",
                   return_value={"success": True}) as text:
            res = send_cta_url("234801", "Body", "https://face.example/x")
        self.assertTrue(res["success"])
        text.assert_called_once()
        self.assertIn("https://face.example/x", text.call_args.args[1])

    def test_the_display_text_is_bounded(self):
        # WhatsApp rejects the whole message if the button label is over 20 chars.
        with patch("whatsapp.providers.requests.post") as post:
            post.return_value.ok = True
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"messages": [{"id": "wamid.1"}]}
            send_cta_url("234801", "Body", "https://face.example/",
                         cta="A very long button label indeed")
        label = self._payload(post)["interactive"]["action"]["parameters"]["display_text"]
        self.assertLessEqual(len(label), 20)

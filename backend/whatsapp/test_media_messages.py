"""Voice notes and photos become words the ordinary router already reads.

Every one of them used to get the same sentence — "I can only read text messages
for now" — which is a channel telling someone it cannot do the thing they just
did. Nigerian customers send voice notes, and they send screenshots of the
account number a friend sent them.

What media buys is a way to SAY something. It never buys a way to skip a step:
the text re-enters handle_inbound exactly as if it had been typed, so it is
parsed, redacted before the intent call, shown on a confirm card and authorised
with a PIN like everything else.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from whatsapp import media


class InterpretTests(TestCase):

    def test_a_voice_note_becomes_its_words(self):
        with patch("whatsapp.llm.transcribes", return_value=True), \
             patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.providers.download_media", return_value=(b"ogg", "audio/ogg")), \
             patch("whatsapp.llm.transcribe_audio", return_value="send 5k to Ada"):
            said, instead, _scanned = media.interpret("audio", "mid-1")
        self.assertEqual(said, "send 5k to Ada")
        self.assertEqual(instead, "")

    def test_a_deployment_that_cannot_hear_says_so_before_downloading(self):
        """No point pulling a megabyte of audio down to a provider with nothing
        to hear it — and the customer gets a sentence they can act on."""
        with patch("whatsapp.llm.transcribes", return_value=False), \
             patch("whatsapp.providers.download_media") as fetch:
            said, instead, _scanned = media.interpret("audio", "mid-1")
        fetch.assert_not_called()
        self.assertEqual(said, "")
        self.assertIn("type your request", instead)

    def test_a_photo_becomes_a_description(self):
        with patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.providers.download_media", return_value=(b"jpg", "image/jpeg")), \
             patch("whatsapp.llm.describe_image", return_value="A slip: GTBank 0123456789"):
            said, instead, _scanned = media.interpret("image", "mid-2")
        self.assertIn("0123456789", said)
        self.assertEqual(instead, "")

    def test_a_caption_leads_and_the_picture_is_context(self):
        """The caption is what the customer actually asked for. Reversed, a model
        reading the pair answers the picture instead of the request."""
        with patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.providers.download_media", return_value=(b"jpg", "image/jpeg")), \
             patch("whatsapp.llm.describe_image", return_value="GTBank 0123456789"):
            said, _instead, _scanned = media.interpret("image", "mid-2", caption="send 5k to this")
        self.assertTrue(said.startswith("send 5k to this"))
        self.assertIn("GTBank 0123456789", said)

    def test_media_we_cannot_fetch_asks_for_words_instead(self):
        with patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.llm.transcribes", return_value=True), \
             patch("whatsapp.providers.download_media", return_value=(b"", "")):
            said, instead, _scanned = media.interpret("audio", "mid-3")
        self.assertEqual(said, "")
        self.assertIn("type it instead", instead)

    def test_an_empty_transcript_is_not_routed_as_an_empty_message(self):
        with patch("whatsapp.llm.transcribes", return_value=True), \
             patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.providers.download_media", return_value=(b"ogg", "audio/ogg")), \
             patch("whatsapp.llm.transcribe_audio", return_value="   "):
            said, instead, _scanned = media.interpret("audio", "mid-4")
        self.assertEqual(said, "")
        self.assertTrue(instead)

    def test_only_audio_and_images_are_claimed(self):
        for kind in ("audio", "voice", "image"):
            self.assertTrue(media.can_interpret(kind))
        for kind in ("document", "sticker", "location", "contacts", "text", ""):
            self.assertFalse(media.can_interpret(kind))


class DownloadMediaTests(TestCase):
    """Meta hands out a short-lived URL from the id, and the URL itself still
    needs the bearer token — an unauthenticated GET returns 401."""

    @override_settings(WHATSAPP={"MODE": "live", "TOKEN": "t", "PHONE_NUMBER_ID": "1",
                                 "BASE_URL": "https://graph.test/v20.0", "ENABLED": True,
                                 "VERIFY_TOKEN": "v", "APP_SECRET": "s"})
    def test_the_second_hop_is_authenticated_too(self):
        from whatsapp import providers

        lookup = type("R", (), {"ok": True, "content": b"{}", "status_code": 200,
                                "json": lambda self: {"url": "https://cdn.test/x",
                                                      "mime_type": "audio/ogg"}})()

        class Streamed:
            ok, status_code, headers = True, 200, {}

            def iter_content(self, _n):
                yield b"bytes"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("whatsapp.providers.wa_live", return_value=True), \
             patch("whatsapp.providers.requests.get",
                   side_effect=[lookup, Streamed()]) as get:
            data, mime = providers.download_media("mid")
        self.assertEqual((data, mime), (b"bytes", "audio/ogg"))
        self.assertIn("Authorization", get.call_args_list[1].kwargs["headers"])

    @override_settings(WHATSAPP={"MODE": "live", "TOKEN": "t", "PHONE_NUMBER_ID": "1",
                                 "BASE_URL": "https://graph.test/v20.0", "ENABLED": True,
                                 "VERIFY_TOKEN": "v", "APP_SECRET": "s"})
    def test_an_oversized_object_is_dropped_rather_than_held_in_memory(self):
        """`file_size` is Meta's claim about the object, not a promise about the
        bytes on the wire, and this runs on a shared worker."""
        from whatsapp import providers

        lookup = type("R", (), {"ok": True, "content": b"{}", "status_code": 200,
                                "json": lambda self: {"url": "https://cdn.test/x",
                                                      "mime_type": "image/jpeg"}})()

        class Firehose:
            ok, status_code, headers = True, 200, {}

            def iter_content(self, _n):
                while True:
                    yield b"x" * (1024 * 1024)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("whatsapp.providers.wa_live", return_value=True), \
             patch("whatsapp.providers.requests.get", side_effect=[lookup, Firehose()]):
            data, _mime = providers.download_media("mid")
        self.assertEqual(data, b"")

    def test_nothing_is_fetched_when_the_channel_is_not_live(self):
        from whatsapp import providers

        with patch("whatsapp.providers.wa_live", return_value=False), \
             patch("whatsapp.providers.requests.get") as get:
            self.assertEqual(providers.download_media("mid"), (b"", ""))
        get.assert_not_called()


class TranscribeCapabilityTests(TestCase):
    """A Claude-only deployment answers voice notes by asking for text rather
    than by pretending to have understood one."""

    def test_anthropic_alone_cannot_transcribe(self):
        from whatsapp import llm

        with patch("whatsapp.llm.active_config", return_value={"provider": "anthropic"}):
            self.assertFalse(llm.transcribes())

    def test_the_openai_compatible_providers_that_serve_it_can(self):
        from whatsapp import llm

        for provider in ("openai", "groq"):
            with patch("whatsapp.llm.active_config", return_value={"provider": provider}):
                self.assertTrue(llm.transcribes(), provider)

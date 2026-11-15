"""The receipts carry the Zitch logo, the 3D Z coil, in white on the teal header.

Not a flat silhouette: the coil's own shading is kept, mapped into a light
range, so the twist still reads as the logo. One asset serves the WhatsApp
receipt, the statement and (as inlined data) the app's receipt PDF.
"""
import io
import os
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from whatsapp import receipt


def _visible(img, floor=250):
    """The RGB of every pixel the coil fully covers."""
    rgba = img.convert("RGBA")
    return [p[:3] for p in rgba.get_flattened_data() if p[3] >= floor]


class CoilAssetTests(SimpleTestCase):
    def test_the_coil_is_committed_trimmed_and_small(self):
        self.assertTrue(os.path.exists(receipt._COIL_PATH))
        self.assertLess(os.path.getsize(receipt._COIL_PATH), 200 * 1024)
        coil = Image.open(receipt._COIL_PATH).convert("RGBA")
        # Trimmed to its edges, so a height asked for is the coil's height.
        self.assertEqual(coil.getchannel("A").getbbox(), (0, 0, coil.width, coil.height))

    def test_the_old_flat_mark_is_gone(self):
        self.assertFalse(hasattr(receipt, "_MARK_PATH"))
        self.assertFalse(os.path.exists(os.path.join(receipt._ASSETS, "zitch-mark.png")))


class WhiteCoilTests(SimpleTestCase):
    def setUp(self):
        receipt._white_coil.cache_clear()
        self.addCleanup(receipt._white_coil.cache_clear)

    def test_white_keeps_the_coils_shape(self):
        coil = Image.open(receipt._COIL_PATH).convert("RGBA")
        white = receipt._white_coil()
        self.assertEqual(white.size, coil.size)
        self.assertEqual(white.getchannel("A").tobytes(), coil.getchannel("A").tobytes())

    def test_white_is_neutral_and_light_but_not_flat(self):
        greys = _visible(receipt._white_coil())
        self.assertTrue(greys)
        self.assertTrue(all(r == g == b for r, g, b in greys))
        levels = [r for r, _g, _b in greys]
        # The approved mapping: luminance into 200..255.
        self.assertGreaterEqual(min(levels), 200)
        self.assertLessEqual(max(levels), 255)
        # ...and the twist still shows: the inner face is visibly darker than
        # the lit band, where a silhouette would be one flat white.
        self.assertGreaterEqual(max(levels) - min(levels), 30)

    def test_load_scales_to_the_height_asked_for(self):
        coil = receipt._load_coil(130)
        self.assertEqual(coil.height, 130)
        source = receipt._white_coil()
        self.assertAlmostEqual(coil.width / coil.height, source.width / source.height, places=2)

    def test_a_missing_asset_still_draws_a_receipt(self):
        with patch.object(receipt, "_COIL_PATH", "/nonexistent/zitch-coil.png"):
            receipt._white_coil.cache_clear()
            self.assertIsNone(receipt._load_coil(100))
            jpeg = receipt._draw_receipt("Airtime receipt", [("Amount", "₦500.00")], "R1")
            pdf = receipt.render_statement_pdf([], balance="₦0.00", generated="now")
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertTrue(pdf.startswith(b"%PDF"))


class ReceiptHeaderTests(SimpleTestCase):
    """The white coil sits straight on the teal band, left of the wordmark."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        jpeg = receipt._draw_receipt("Transfer receipt", [("Amount", "₦25,000.00"),
                                                          ("Reference", "ZTC-1")], "ZTC-1")
        cls.img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        coil = receipt._load_coil(receipt._px(48))
        left = receipt._px(34 + 44)
        top = receipt._px(64) - coil.height // 2
        cls.coil = coil
        cls.box = (left, top, left + coil.width, top + coil.height)

    def _pixels_under(self, alpha_test):
        x0, y0 = self.box[:2]
        alpha = self.coil.getchannel("A")
        out = []
        for y in range(self.coil.height):
            for x in range(self.coil.width):
                if alpha_test(alpha.getpixel((x, y))):
                    out.append(self.img.getpixel((x0 + x, y0 + y)))
        return out

    def test_the_coil_is_drawn_in_white(self):
        covered = self._pixels_under(lambda a: a == 255)
        light = [p for p in covered if min(p) >= 185 and max(p) - min(p) <= 16]
        self.assertGreater(len(light) / len(covered), 0.9)

    def test_its_shading_survives(self):
        covered = self._pixels_under(lambda a: a == 255)
        levels = sorted(sum(p) / 3 for p in covered)
        # The darkest tenth (the inner face) sits well under the lightest tenth.
        tenth = len(levels) // 10
        dark = sum(levels[:tenth]) / tenth
        lit = sum(levels[-tenth:]) / tenth
        self.assertGreater(lit - dark, 15)

    def test_there_is_no_disc_behind_it(self):
        # Outside the coil, inside its box, the band shows through: teal.
        bare = self._pixels_under(lambda a: a == 0)
        self.assertTrue(bare)
        teal = [p for p in bare if p[1] - p[0] > 40 and p[2] - p[0] > 30]
        self.assertGreater(len(teal) / len(bare), 0.95)

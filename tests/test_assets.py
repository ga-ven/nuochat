import struct
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.assets = Path(__file__).resolve().parents[1] / "assets"

    def test_mascot_svg_is_valid(self):
        root = ET.parse(self.assets / "icon.svg").getroot()
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.attrib["viewBox"], "0 0 512 512")
        self.assertTrue((self.assets / "icon.png").stat().st_size > 1000)

    def test_windows_icon_contains_required_sizes(self):
        data = (self.assets / "icon.ico").read_bytes()
        reserved, icon_type, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, icon_type), (0, 1))
        sizes = set()
        for index in range(count):
            width, height = struct.unpack_from("<BB", data, 6 + index * 16)
            sizes.add((width or 256, height or 256))
        self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes)


if __name__ == "__main__":
    unittest.main()

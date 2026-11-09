#!/usr/bin/env python3
"""No-game-data regression tests for the Linux NSP import helper."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_game


def example_romfs(path, name="test.txt", contents=b"hello"):
    """Generate the smallest valid synthetic RomFS tables and payload."""
    filename = name.encode("utf-8")
    header = struct.pack("<10Q", 0x50, 0, 0, 0x80, 0x18, 0, 0,
                         0xA0, 0x20 + len(filename), 0x100)
    root = struct.pack("<6I", 0, 0xffffffff, 0xffffffff, 0, 0xffffffff, 0)
    entry = struct.pack("<IIQQII", 0, 0xffffffff, 0, len(contents), 0xffffffff, len(filename))
    image = bytearray(0x100 + len(contents))
    image[:len(header)] = header
    image[0x80:0x80 + len(root)] = root
    image[0xa0:0xa0 + len(entry) + len(filename)] = entry + filename
    image[0x100:] = contents
    path.write_bytes(image)


class ImportTest(unittest.TestCase):
    def test_materialize(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            src, dest = work / "demo.romfs", work / "expanded"
            example_romfs(src)
            import_game.materialize(src, dest)
            self.assertEqual((dest / "test.txt").read_bytes(), b"hello")

    def test_path_traversal_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            image = work / "demo.romfs"
            image.write_bytes(b"hello")
            with patch.object(import_game, "index_romfs", return_value=[
                dict(path="../escape.txt", offset=0, size=5)
            ]):
                with self.assertRaisesRegex(ValueError, "Unsafe RomFS path"):
                    import_game.materialize(image, work / "output")
            self.assertFalse((work / "escape.txt").exists())

    def test_game_ids(self):
        from nsp_vendor import extract_exefs
        section = bytearray(0xC00)
        section[0x404] = 4
        for title in ("01007EF00011E000", "01007EF00011E800"):
            record = {"content_type": 0, "title_id": title}
            selected, _ = extract_exefs.program_nca([(record, section)], update=True)
            self.assertIs(selected, record)
        with self.assertRaisesRegex(ValueError, "another game"):
            extract_exefs.program_nca([({"content_type": 0, "title_id": "0000000000000000"}, section)], update=True)

    def test_materialize_progress(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            image = work / "demo.romfs"
            example_romfs(image)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                import_game.materialize(image, work / "expanded")
            self.assertIn("Extracted 0/1 RomFS files...", output.getvalue())
            self.assertIn("RomFS file 1/1: test.txt (5 bytes)", output.getvalue())

    def test_missing_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            with self.assertRaisesRegex(ValueError, "File not found"):
                import_game.import_game(work / "base.nsp", work / "update.nsp",
                                        work / "prod.keys", work / "hactool",
                                        work / "stage")


if __name__ == "__main__":
    unittest.main()

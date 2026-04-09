import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tofufy.render import replace_inline_marked_block, replace_marked_block


class MarkerReplacementTests(unittest.TestCase):
    def test_replace_marked_block_inserts_when_missing(self) -> None:
        generated = "# BEGIN\nhello\n# END\n"
        self.assertEqual(replace_marked_block("", "# BEGIN", "# END", generated), generated)

    def test_replace_marked_block_replaces_existing_region_only(self) -> None:
        original = "prefix\n# BEGIN\nold\n# END\nsuffix\n"
        generated = "# BEGIN\nnew\n# END\n"
        expected = "prefix\nsuffix\n\n# BEGIN\nnew\n# END\n"
        self.assertEqual(replace_marked_block(original, "# BEGIN", "# END", generated), expected)

    def test_replace_inline_marked_block_preserves_marker_position(self) -> None:
        original = "a\n  # BEGIN\n  old = 1\n  # END\nb\n"
        expected = "a\n  # BEGIN\n  new = 2\n  # END\nb\n"
        self.assertEqual(
            replace_inline_marked_block(original, "# BEGIN", "# END", "  new = 2"),
            expected,
        )


if __name__ == "__main__":
    unittest.main()

import unittest

from airlock.edits import EditError, apply_block, parse_edits, strip_edits

FILES = {"a.py": "def f(x):\n    if x:\n        return 1\n    return 0\n", "b.py": "X = 1\nY = 1\n"}


def block(search, replace):
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n"


class EditsTest(unittest.TestCase):
    def test_exact_and_multiple_blocks_in_one_file(self):
        reply = "FILE: b.py\n" + block("X = 1", "X = 2") + block("Y = 1", "Y = 3")
        self.assertEqual(parse_edits(reply, FILES, None), {"b.py": "X = 2\nY = 3\n"})

    def test_default_file_when_no_file_line(self):
        self.assertEqual(parse_edits(block("X = 1", "X = 5"), FILES, "b.py"), {"b.py": "X = 5\nY = 1\n"})

    def test_indentation_shift_is_rebased(self):
        out = apply_block(FILES["a.py"], ["if x:", "    return 1"], ["if x > 0:", "    return 1"], "a.py")
        self.assertEqual(out, "def f(x):\n    if x > 0:\n        return 1\n    return 0\n")

    def test_errors(self):
        cases = {
            "not found": ("FILE: b.py\n" + block("Z = 1", "Z = 2"), "not found"),
            "ambiguous": ("FILE: b.py\n" + block("= 1", "x"), "not found"),
            "unknown file": ("FILE: c.py\n" + block("X = 1", "X = 2"), "repository files"),
            "unclosed": ("FILE: b.py\n<<<<<<< SEARCH\nX = 1\n=======\nX = 2\n", "not closed"),
            "nothing": ("just words", "no SEARCH/REPLACE"),
        }
        for name, (reply, msg) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(EditError, msg):
                parse_edits(reply, FILES, None)

    def test_repeated_lines_are_ambiguous(self):
        with self.assertRaisesRegex(EditError, "matches 2 places"):
            apply_block("a = 1\na = 1\n", ["a = 1"], ["a = 2"], "x.py")

    def test_full_file_fallback(self):
        self.assertEqual(parse_edits("FILE: b.py\n```python\nX = 9\n```", FILES, None), {"b.py": "X = 9\n"})

    def test_strip_edits_leaves_explanation(self):
        reply = "X is wrong.\nFILE: b.py\n```python\n" + block("X = 1", "X = 2") + "```\nDone."
        self.assertEqual(" ".join(strip_edits(reply).split()), "X is wrong. Done.")


if __name__ == "__main__":
    unittest.main()

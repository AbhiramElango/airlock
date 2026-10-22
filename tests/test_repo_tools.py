import os
import tempfile
import unittest
from pathlib import Path

from airlock.config import Limits
from airlock.repo_tools import AccessDenied, RepoTools


class RepoToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "repo"
        self.outside = base / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        (self.outside / "leak.py").write_text("SECRET = 1\n")
        files = {
            "utils.py": "def f():\n    return 1\n",
            "pkg/mod.py": "x = 'needle'\n",
            ".git/config": "[core]\n",
            ".venv/lib.py": "venv\n",
            ".env": "API_KEY=abc\n",
            "server.pem": "-----BEGIN-----\n",
            "big.py": "a" * 200,
        }
        for rel, text in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        (self.root / "blob.py").write_bytes(b"ab\0cd")
        os.symlink(self.outside / "leak.py", self.root / "link.py")
        os.symlink(self.outside, self.root / "linkdir")
        self.tools = RepoTools(self.root, Limits(max_file_bytes=100))

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_inside_root(self):
        self.assertIn("return 1", self.tools.read_file("utils.py"))
        self.assertIn("needle", self.tools.read_file("pkg/mod.py"))

    def test_rejects_traversal_and_absolute(self):
        for bad in ["../outside/leak.py", "pkg/../../outside/leak.py", "/etc/passwd",
                    str(self.outside / "leak.py"), "~/.ssh/id_rsa", "..\\outside\\leak.py"]:
            with self.subTest(bad=bad), self.assertRaises(AccessDenied):
                self.tools.read_file(bad)

    def test_rejects_symlink_escape(self):
        for bad in ["link.py", "linkdir/leak.py"]:
            with self.subTest(bad=bad), self.assertRaises(AccessDenied):
                self.tools.read_file(bad)

    def test_rejects_ignored_secret_binary_large(self):
        for bad in [".git/config", ".venv/lib.py", ".env", "server.pem", "blob.py", "big.py", "nope.py"]:
            with self.subTest(bad=bad), self.assertRaises(AccessDenied):
                self.tools.read_file(bad)

    def test_list_files_is_filtered(self):
        self.assertEqual(self.tools.list_files(), ["pkg/mod.py", "utils.py"])

    def test_list_files_limit(self):
        tools = RepoTools(self.root, Limits(max_file_bytes=100, max_files=1))
        self.assertEqual(len(tools.list_files()), 1)

    def test_search(self):
        hits = self.tools.search("NEEDLE")
        self.assertEqual([(h.path, h.line_no) for h in hits], [("pkg/mod.py", 1)])
        self.assertEqual(self.tools.search("SECRET"), [])

    def test_root_must_exist(self):
        with self.assertRaises(OSError):
            RepoTools(self.root / "missing")


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path

from airlock.patcher import GitWorkspace, PatchError, build_diff, build_patch, diff_stats, validate_patch
from airlock.repo_tools import RepoTools
from tests.fakes import FIXED, ORIGINAL, git, make_git_repo


class DiffTest(unittest.TestCase):
    def test_build_diff(self):
        diff = build_diff("utils.py", ORIGINAL, FIXED)
        self.assertTrue(diff.startswith("--- a/utils.py\n+++ b/utils.py\n@@"))
        self.assertEqual(diff_stats(diff), (1, 1))
        self.assertEqual(build_diff("utils.py", ORIGINAL, ORIGINAL), "")

    def test_missing_trailing_newline_is_marked(self):
        self.assertIn("\\ No newline at end of file", build_diff("u.py", "x = 1", "x = 2\n"))


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        make_git_repo(self.root, {"utils.py": ORIGINAL, "keep.py": "k = 1\n"})
        self.tools = RepoTools(self.root)
        self.ws = GitWorkspace(self.root)
        self.diff = build_diff("utils.py", ORIGINAL, FIXED)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_repo_returns_head(self):
        self.assertRegex(self.ws.require_clean(), r"^[0-9a-f]{7,}$")

    def test_dirty_or_untracked_tree_rejected(self):
        for name in ["utils.py", "new.py"]:
            with self.subTest(name=name):
                (self.root / name).write_text("changed\n")
                with self.assertRaises(PatchError):
                    self.ws.require_clean()
                git(self.root, "checkout", "--", ".")
                git(self.root, "clean", "-fq")

    def test_non_git_and_subdir_rejected(self):
        with tempfile.TemporaryDirectory() as plain, self.assertRaises(PatchError):
            GitWorkspace(Path(plain).resolve()).require_clean()
        (self.root / "sub").mkdir()
        with self.assertRaises(PatchError):
            GitWorkspace(self.root / "sub").require_clean()

    def test_apply(self):
        validate_patch(self.diff, ["utils.py"], self.tools, self.ws)
        self.ws.apply(self.diff)
        self.assertEqual((self.root / "utils.py").read_text(), FIXED)
        self.assertEqual((self.root / "keep.py").read_text(), "k = 1\n")

    def test_apply_two_files(self):
        diff = build_patch({"utils.py": (ORIGINAL, FIXED), "keep.py": ("k = 1\n", "k = 2\n")})
        validate_patch(diff, ["utils.py", "keep.py"], self.tools, self.ws)
        self.ws.apply(diff)
        self.assertEqual(((self.root / "utils.py").read_text(), (self.root / "keep.py").read_text()), (FIXED, "k = 2\n"))

    def test_stale_patch_not_applied(self):
        (self.root / "utils.py").write_text("something else\n")
        with self.assertRaises(PatchError):
            self.ws.apply(self.diff)
        self.assertEqual((self.root / "utils.py").read_text(), "something else\n")

    def test_validate_rejects_bad_patches(self):
        other = build_diff("keep.py", "k = 1\n", "k = 2\n")
        cases = {
            "empty": "",
            "wrong target": other,
            "extra file": self.diff + other,
            "hidden header": self.diff + "--- a/keep.py\n+++ b/keep.py\n@@ -1 +1 @@\n-k = 1\n+k = 9\n",
            "traversal": self.diff.replace("a/utils.py", "a/../x.py").replace("b/utils.py", "b/../x.py"),
            "absolute": self.diff.replace("a/utils.py", "/etc/passwd"),
        }
        for name, diff in cases.items():
            with self.subTest(name), self.assertRaises(PatchError):
                validate_patch(diff, ["utils.py"], self.tools, self.ws)

    def test_validate_rejects_target_outside_root(self):
        with tempfile.TemporaryDirectory() as out:
            os.symlink(Path(out) / "x.py", self.root / "link.py")
            diff = build_diff("link.py", "a\n", "b\n")
            with self.assertRaises(PatchError):
                validate_patch(diff, ["link.py"], self.tools, self.ws)


if __name__ == "__main__":
    unittest.main()

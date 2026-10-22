import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from airlock.audit import AuditLog, redact
from airlock.verify import RunResult, TestRunner, check_note, improves


class TestRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, body):
        (self.root / "test_x.py").write_text(body)

    def test_pass_fail_and_no_tests(self):
        runner = TestRunner()
        self.assertEqual(runner.run(self.root).status, "no_tests")
        self.write("def test_ok():\n    assert True\n")
        result = runner.run(self.root)
        self.assertTrue(result.passed)
        self.assertIn("1 passed", result.summary)
        self.write("def test_bad():\n    assert 1 == 2\n")
        result = runner.run(self.root)
        self.assertEqual(result.status, "failed")
        self.assertIn("assert 1 == 2", result.output)

    def test_timeout(self):
        self.write("import time\n\ndef test_slow():\n    time.sleep(10)\n")
        result = TestRunner(timeout_s=1).run(self.root)
        self.assertEqual(result.status, "timeout")
        self.assertLess(result.seconds, 5)

    def test_no_bytecode_or_cache_left_behind(self):
        self.write("def test_ok():\n    pass\n")
        TestRunner().run(self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["test_x.py"])

    def test_env_is_scrubbed(self):
        self.write("import os\n\ndef test_env():\n    assert 'OPENAI_API_KEY' not in os.environ\n")
        with unittest.mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-should-not-leak"}):
            self.assertTrue(TestRunner().run(self.root).passed)

    def test_output_is_capped(self):
        self.write("def test_bad():\n    assert 'x' * 9000 == ''\n")
        self.assertLessEqual(len(TestRunner(max_output_chars=500).run(self.root).output), 510)

    def test_fixed_command(self):
        self.assertEqual(TestRunner().command[1:], ["-m", "pytest", "-q", "-p", "no:cacheprovider"])
        self.assertEqual(TestRunner().command[0], sys.executable)


class AuditTest(unittest.TestCase):
    def test_redact(self):
        text = ("API_KEY=abc123 password: 'hunter2' Authorization: Bearer eyJhbGciOi "
                "AKIAABCDEFGHIJKLMNOP ghp_abcdefghijklmnopqrstuvwx /home/alice/repo /Users/bob/x "
                "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----")
        out = redact({"t": [text]})["t"][0]
        for leaked in ["abc123", "hunter2", "eyJhbGciOi", "AKIAABCDEFGHIJKLMNOP", "ghp_abc", "alice", "bob", "MIIE"]:
            self.assertNotIn(leaked, out)
        self.assertIn("~/repo", out)
        self.assertEqual(redact("SECRET_TOKEN=xyz"), "SECRET_TOKEN=[REDACTED]")
        self.assertEqual(redact("158 tokens\n\n== Proposed =="), "158 tokens\n\n== Proposed ==")

    def test_jsonl_record(self):
        with tempfile.TemporaryDirectory() as d:
            log = AuditLog(Path(d))
            run = log.start(task="t", repo="/home/alice/r")
            run.set(approved=True, diff="+SECRET_TOKEN=xyz\n")
            run.finish("rejected")
            log.start(task="t2").finish("setup_error")
            recs = [json.loads(l) for l in log.path.read_text().splitlines()]
        self.assertEqual([r["outcome"] for r in recs], ["rejected", "setup_error"])
        self.assertEqual(recs[0]["repo"], "~/r")
        self.assertNotIn("xyz", recs[0]["diff"])
        for key in ["started_at", "finished_at", "total_seconds"]:
            self.assertIn(key, recs[0])



class ImprovesTest(unittest.TestCase):
    @staticmethod
    def run_result(status, *failed, interrupted=False):
        lines = [f"FAILED {t} - assert 0" for t in failed] + (["Interrupted: 1 error"] if interrupted else [])
        return RunResult(status, 0 if status == "passed" else 1, 0.1, "\n".join(lines))

    def test_improves(self):
        before = self.run_result("failed", "t.py::a", "t.py::b")
        cases = {
            "all pass": (self.run_result("passed"), True),
            "one fixed": (self.run_result("failed", "t.py::b"), True),
            "nothing fixed": (self.run_result("failed", "t.py::a", "t.py::b"), False),
            "breaks another": (self.run_result("failed", "t.py::b", "t.py::c"), False),
            "collection error": (self.run_result("failed", interrupted=True), False),
            "timeout": (self.run_result("timeout"), False),
        }
        for name, (after, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(improves(before, after), expected)
        self.assertEqual(check_note(before, cases["one fixed"][0]),
                         "fixes 1 failing test(s), breaks none; 1 still fail (other issues)")

    def test_collection_error_before_needs_a_full_pass(self):
        before = self.run_result("failed", "t.py", interrupted=True)
        self.assertFalse(improves(before, self.run_result("failed", "t.py::a")))
        self.assertTrue(improves(before, self.run_result("passed")))

    def test_passing_before_must_still_pass(self):
        self.assertFalse(improves(self.run_result("passed"), self.run_result("failed", "t.py::a")))


if __name__ == "__main__":
    unittest.main()

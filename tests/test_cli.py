import argparse
import contextlib
import io
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from airlock import cli
from airlock.audit import AuditLog
from airlock.config import Config
from tests.fakes import FIXED, ORIGINAL, FakeLLM, FakeRunner, fenced, git, make_git_repo

TASK = "Fix the failing edge case in utils.py"


class CliFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "repo"
        self.root.mkdir()
        make_git_repo(self.root, {"utils.py": ORIGINAL, "test_utils.py": "from utils import chunk\n"})
        self.audit_dir = Path(self.tmp.name) / "audit_out"
        self.args = argparse.Namespace(task=TASK, repo=str(self.root), model="fake", no_color=True)

    def tearDown(self):
        self.tmp.cleanup()

    def audit_records(self):
        return [json.loads(l) for l in (self.audit_dir / "audit.jsonl").read_text().splitlines()]

    def run_cli(self, llm, answer, after_status="passed", checks=("passed",)):
        asked = []

        def approve(prompt):
            asked.append(prompt)
            return answer

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.runner = FakeRunner(after_status, checks)
            deps = cli.Deps(llm, approve, self.runner, AuditLog(self.audit_dir))
            code = cli.run(self.args, Config(), deps)
        return code, out.getvalue(), err.getvalue(), asked

    def test_approved_patch_is_applied(self):
        code, out, _, asked = self.run_cli(FakeLLM(fenced(FIXED)), True)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(len(asked), 1)
        self.assertIn("+++ b/utils.py", out)
        self.assertEqual((self.root / "utils.py").read_text(), FIXED)
        rec = self.audit_records()[-1]
        self.assertEqual((rec["outcome"], rec["approved"], rec["targets"]), ("applied_tests_passed", True, ["utils.py"]))
        self.assertEqual((rec["tests_before"]["status"], rec["tests_after"]["status"]), ("failed", "passed"))
        self.assertIn("+++ b/utils.py", rec["diff"])

    def test_failed_self_check_gets_a_second_round(self):
        still_wrong = fenced(ORIGINAL.replace("- n + 1", "- n"))
        llm = FakeLLM(still_wrong, fenced(FIXED))
        code, out, _, asked = self.run_cli(llm, True, checks=("failed", "passed"))
        self.assertEqual((code, len(asked), len(llm.prompts)), (cli.EXIT_OK, 1, 2))
        self.assertIn("With that change the tests still fail", llm.prompts[1])
        self.assertEqual((self.root / "utils.py").read_text(), FIXED)
        self.assertEqual([c["status"] for c in self.audit_records()[-1]["self_checks"]], ["failed", "passed"])
        self.assertNotEqual(self.runner.roots[1], self.root)  # self-check ran on a scratch copy

    def test_self_check_still_failing_warns_before_approval(self):
        still_wrong = fenced(ORIGINAL.replace("- n + 1", "- n"))
        code, out, _, _ = self.run_cli(FakeLLM(still_wrong, still_wrong), False, checks=("failed", "failed"))
        self.assertEqual(code, cli.EXIT_REJECTED)
        self.assertIn("warning: the tests still fail with this change", out)

    def test_failing_tests_after_apply(self):
        code, out, _, _ = self.run_cli(FakeLLM(fenced(FIXED)), True, after_status="failed")
        self.assertEqual(code, cli.EXIT_TESTS)
        self.assertIn("1 failed", out)
        self.assertEqual(self.audit_records()[-1]["outcome"], "applied_tests_failed")

    def test_failing_output_reaches_model(self):
        llm = FakeLLM(fenced(FIXED))
        self.run_cli(llm, False)
        self.assertIn("current test output (failing)", llm.prompts[0])
        self.assertIn("assert chunk", llm.prompts[0])

    def test_rejected_patch_changes_nothing(self):
        code, out, _, _ = self.run_cli(FakeLLM(fenced(FIXED)), False)
        self.assertEqual(code, cli.EXIT_REJECTED)
        self.assertIn("rejected", out)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        rec = self.audit_records()[-1]
        self.assertEqual((rec["outcome"], rec["approved"]), ("rejected", False))
        self.assertNotIn("tests_after", rec)

    def test_dirty_tree_stops_before_model(self):
        (self.root / "utils.py").write_text("dirty\n")
        llm = FakeLLM()
        code, _, err, asked = self.run_cli(llm, True)
        self.assertEqual(code, cli.EXIT_SETUP)
        self.assertIn("not clean", err)
        self.assertEqual((llm.prompts, asked), ([], []))
        self.assertEqual(self.audit_records()[-1]["outcome"], "setup_error")

    def test_bad_proposal_never_asks(self):
        code, _, _, asked = self.run_cli(FakeLLM(fenced("def broken(:\n"), fenced("def broken(:\n")), True)
        self.assertEqual((code, asked), (cli.EXIT_PROPOSAL, []))

    def test_answer_only(self):
        code, out, _, asked = self.run_cli(FakeLLM("The repo has utils.py and its tests.", intent="QUESTION"), True)
        self.assertEqual((code, asked), (cli.EXIT_OK, []))
        self.assertIn("== Answer ==\nThe repo has utils.py and its tests.", out)
        rec = self.audit_records()[-1]
        self.assertEqual((rec["outcome"], rec["answer"]), ("answered", "The repo has utils.py and its tests."))
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_explanation_shown_and_audited(self):
        reply = "The range stops too early.\n```python\n" + FIXED + "```"
        _, out, _, _ = self.run_cli(FakeLLM(reply), False)
        self.assertIn("== What is wrong ==\nThe range stops too early.", out)
        self.assertEqual(self.audit_records()[-1]["explanation"], "The range stops too early.")

    def test_task_asked_interactively(self):
        with unittest.mock.patch("builtins.input", return_value=""), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["--repo", str(self.root)]), cli.EXIT_SETUP)
        self.assertIn("no task given", err.getvalue())
        with unittest.mock.patch("builtins.input", return_value="fix utils.py") as ask, \
                unittest.mock.patch.object(cli, "run", return_value=0) as run:
            self.assertEqual(cli.main(["--repo", str(self.root)]), 0)
        ask.assert_called_once_with("What should I do? ")
        self.assertEqual(run.call_args.args[0].task, "fix utils.py")

    def test_unquoted_task_words_are_joined(self):
        with unittest.mock.patch.object(cli, "run", return_value=0) as run:
            cli.main(["--repo", str(self.root), "what", "is", "wrong", "with", "utils.py?"])
        self.assertEqual(run.call_args.args[0].task, "what is wrong with utils.py?")

    def test_repo_required_unless_warm_up(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["fix utils.py"]), cli.EXIT_SETUP)
        self.assertIn("--repo is required", err.getvalue())
        with unittest.mock.patch.object(cli.OllamaClient, "warm_up") as warm:
            self.assertEqual(cli.main(["--warm-up"]), cli.EXIT_OK)
        warm.assert_called_once()

    def test_console_approver(self):
        for reply, expected in [("y", True), ("YES", True), ("", False), ("n", False), ("yep", False)]:
            with self.subTest(reply=reply), unittest.mock.patch("builtins.input", return_value=reply):
                self.assertIs(cli.console_approver("? "), expected)
        with unittest.mock.patch("builtins.input", side_effect=EOFError):
            self.assertFalse(cli.console_approver("? "))


if __name__ == "__main__":
    unittest.main()

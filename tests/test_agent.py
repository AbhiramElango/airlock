import tempfile
import unittest
from pathlib import Path

from airlock.agent import Agent, ProposalError, RepoContextSelector, extract_explanation
from airlock.config import Config, ModelConfig
from airlock.llm import OllamaClient, require_loopback
from airlock.repo_tools import RepoTools
from tests.fakes import FIXED, ORIGINAL, FakeLLM, fenced


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "utils.py").write_text(ORIGINAL)
        (root / "test_utils.py").write_text("from utils import chunk\n")
        (root / "other.py").write_text("print('chunk')\n")
        self.tools = RepoTools(root)

    def tearDown(self):
        self.tmp.cleanup()

    def agent(self, *replies):
        return Agent(FakeLLM(*replies), self.tools, Config())

    def test_context_has_all_files_mentioned_first(self):
        ctx = RepoContextSelector().select("Fix the bug in utils.py", self.tools)
        self.assertEqual(ctx.target, "utils.py")
        self.assertEqual(list(ctx.files), ["utils.py", "test_utils.py", "other.py"])

    def test_no_file_mentioned_still_sees_everything(self):
        ctx = RepoContextSelector().select("what files do you have?", self.tools)
        self.assertIsNone(ctx.target)
        self.assertEqual(sorted(ctx.files), ["other.py", "test_utils.py", "utils.py"])

    def test_falls_back_to_keyword_match(self):
        ctx = RepoContextSelector().select("chunk drops the remainder", self.tools)
        self.assertIn(ctx.target, ("utils.py", "other.py"))

    def test_proposal_and_prompt(self):
        agent = self.agent(fenced(FIXED))
        p = agent.propose("Fix the failing edge case in utils.py")
        self.assertEqual(p.changes, {"utils.py": (ORIGINAL, FIXED)})
        self.assertIn("--- other.py ---", agent.llm.prompts[0])
        self.assertIn("The user mentions utils.py", agent.llm.prompts[0])

    def test_plain_answer_drops_code(self):
        agent = self.agent("I can see utils.py, other.py and\n test_utils.py.\n```python\nx = 1\n```")
        p = agent.answer("what files do you have?")
        self.assertEqual((p.changes, p.answer), ({}, "I can see utils.py, other.py and test_utils.py."))
        self.assertIn("Do not change or rewrite any code", agent.llm.prompts[0])

    def test_classify(self):
        for reply, expected in [("FIX", "fix"), ("fix.", "fix"), ("QUESTION", "question"),
                                ("I think FIX", "question"), ("", "question")]:
            with self.subTest(reply=reply):
                self.assertEqual(Agent(FakeLLM(intent=reply), self.tools, Config()).classify("x"), expected)

    def test_file_line_picks_target(self):
        reply = "other.py prints the wrong word.\nFILE: other.py\n```python\nprint('chunks')\n```"
        p = self.agent(reply).propose("something prints the wrong word")
        self.assertEqual(p.changes, {"other.py": ("print('chunk')\n", "print('chunks')\n")})
        self.assertEqual(p.explanation, "other.py prints the wrong word.")

    def test_rejects_test_file_and_unknown_file_then_retries(self):
        for bad_target in ["test_utils.py", "../evil.py", "nope.py"]:
            with self.subTest(target=bad_target):
                agent = self.agent(f"FILE: {bad_target}\n```python\nx = 1\n```", "FILE: utils.py\n" + fenced(FIXED))
                p = agent.propose("fix it")
                self.assertEqual((p.targets, len(p.completions)), (["utils.py"], 2))
                self.assertIn("previous reply was rejected", agent.llm.prompts[1])

    def test_rejects_syntax_error_and_noop(self):
        for bad in ["def broken(:\n", ORIGINAL]:
            with self.subTest(bad=bad), self.assertRaises(ProposalError):
                self.agent(fenced(bad), fenced(bad)).propose("fix utils.py")

    def test_explanation_is_returned(self):
        reply = "1. The loop stops early, so the last item is lost.\n2. FILE: utils.py\n3.\n```python\n" + FIXED + "```"
        p = self.agent(reply).propose("what is wrong with utils.py?")
        self.assertEqual(p.explanation, "The loop stops early, so the last item is lost.")

    def test_extract_explanation(self):
        self.assertEqual(extract_explanation("```python\nx = 1\n```"), "")
        self.assertEqual(extract_explanation("Too  big\n tip.\n```\nx\n```\nDone."), "Too big tip. Done.")
        self.assertEqual(len(extract_explanation("word " * 500)), 400)
        self.assertEqual(extract_explanation("Tax is 100x too big. ONE short plain-English sentence (max 25 words)"),
                         "Tax is 100x too big.")
        self.assertEqual(extract_explanation("The tip is 100x too big. bill.py:"), "The tip is 100x too big.")

    def test_self_check_feedback_names_broken_tests(self):
        from airlock.agent import self_check_feedback
        note = self_check_feedback("diff", "1 failed", {"test_models.py::test_user_fields"})
        self.assertIn("broke these tests, which passed before it: test_models.py::test_user_fields", note)
        self.assertNotIn("broke these tests", self_check_feedback("diff", "1 failed"))

    def test_search_replace_across_two_files(self):
        reply = ("The range stops early and the word is wrong.\n"
                 "FILE: utils.py\n<<<<<<< SEARCH\n    return [xs[i:i + n] for i in range(0, len(xs) - n + 1, n)]\n"
                 "=======\n    return [xs[i:i + n] for i in range(0, len(xs), n)]\n>>>>>>> REPLACE\n"
                 "FILE: other.py\n```python\n<<<<<<< SEARCH\nprint('chunk')\n=======\nprint('chunks')\n"
                 ">>>>>>> REPLACE\n```")
        p = self.agent(reply).propose("fix it")
        self.assertEqual(p.changes, {"utils.py": (ORIGINAL, FIXED),
                                     "other.py": ("print('chunk')\n", "print('chunks')\n")})
        self.assertEqual(p.explanation, "The range stops early and the word is wrong.")

    def test_bad_search_block_is_retried_with_feedback(self):
        bad = "FILE: utils.py\n<<<<<<< SEARCH\nnot in the file\n=======\nx = 1\n>>>>>>> REPLACE"
        good = "FILE: utils.py\n" + fenced(FIXED)
        agent = self.agent(bad, good)
        self.assertEqual(agent.propose("fix utils.py").targets, ["utils.py"])
        self.assertIn("SEARCH text not found in utils.py", agent.llm.prompts[1])

    def test_too_many_files_rejected(self):
        block = "FILE: {f}\n<<<<<<< SEARCH\n{a}\n=======\n{b}\n>>>>>>> REPLACE\n"
        reply = (block.format(f="utils.py", a="def chunk(xs, n):", b="def chunk(xs, n):  # x")
                 + block.format(f="other.py", a="print('chunk')", b="print('x')"))
        agent = Agent(FakeLLM(reply, reply), self.tools, Config(max_files_per_fix=1))
        with self.assertRaisesRegex(ProposalError, "at most 1 files"):
            agent.propose("fix it")


class LoopbackTest(unittest.TestCase):
    def test_only_loopback_allowed(self):
        for ok in ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"]:
            require_loopback(ok)
        for bad in ["http://192.0.2.10:11434", "https://api.example.com", "http://0.0.0.0:11434"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                OllamaClient(ModelConfig(url=bad))


if __name__ == "__main__":
    unittest.main()

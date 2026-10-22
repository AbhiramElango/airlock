import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from airlock.audit import AuditLog
from airlock.config import Config
from airlock.repo_tools import RepoTools
from airlock.session import Session
from airlock.verify import CommandRunner
from airlock.web.server import AgentUI, Handler, NetworkProbe
from airlock.web.terminal import DemoTerminal
from tests.fakes import FIXED, ORIGINAL, FakeLLM, FakeRunner, make_git_repo


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "repo"
        self.root.mkdir()
        make_git_repo(self.root, {"utils.py": ORIGINAL + "print('hello')\n", "test_utils.py": "x = 1\n"})
        tools = RepoTools(self.root)
        self.llm = FakeLLM("The range stops early.\n```python\n" + FIXED + "print('hello')\n```")
        runner = FakeRunner("passed")
        session = Session(self.root, Config(), self.llm, runner, AuditLog(base / "audit"))
        commands = CommandRunner(timeout_s=5)
        Handler.ui = AgentUI(session, tools, commands, DemoTerminal(tools, commands, runner))
        Handler.probe = NetworkProbe()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        Handler.allowed_hosts = {f"127.0.0.1:{self.port}"}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, host=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if body is not None:
            headers["Content-Type"] = ctype
        conn.request("GET" if body is None else "POST", path,
                     None if body is None else json.dumps(body), headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, (json.loads(data) if res.getheader("Content-Type", "").startswith("application/json")
                            else data.decode())

    def wait_job(self):
        for _ in range(100):
            status, job = self.call("/api/job")
            if job["status"] != "working":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_static_and_files(self):
        status, page = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("<title>Airlock</title>", page)
        self.assertEqual(self.call("/api/files")[1]["files"], ["test_utils.py", "utils.py"])
        self.assertIn("def chunk", self.call("/api/file?path=utils.py")[1]["text"])

    def test_rejects_bad_host_traversal_and_non_json(self):
        self.assertEqual(self.call("/api/files", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("/api/file?path=../x.py")[0], 403)
        self.assertEqual(self.call("/api/run", {"path": "x"}, ctype="text/plain")[0], 415)
        for bad in ["../x.py", "/etc/passwd", "README.md", "missing.py"]:
            with self.subTest(bad=bad):
                self.assertEqual(self.call("/api/run", {"path": bad})[0], 409)

    def test_run_script(self):
        status, data = self.call("/api/run", {"path": "utils.py"})
        self.assertEqual((status, data["result"]["status"]), (200, "ok"))
        self.assertIn("hello", data["result"]["output"])

    def test_ask_then_approve(self):
        self.assertEqual(self.call("/api/approve", {})[0], 409)
        self.assertEqual(self.call("/api/ask", {"task": "  "})[0], 409)
        self.call("/api/ask", {"task": "what is wrong with utils.py?"})
        job = self.wait_job()
        self.assertEqual((job["status"], job["targets"]), ("pending", ["utils.py"]))
        self.assertEqual(job["explanation"], "The range stops early.")
        self.assertIn("+    return [xs[i:i + n] for i in range(0, len(xs), n)]", job["diff"])
        self.assertEqual([s["text"].split()[0] for s in job["steps"]], ["Reading", "Understood", "Tests", "Model", "Self-check:"])
        self.assertEqual((self.root / "utils.py").read_text(), ORIGINAL + "print('hello')\n")
        status, done = self.call("/api/approve", {})
        self.assertEqual((status, done["status"], done["tests_after"]["status"]), (200, "done", "passed"))
        self.assertEqual((self.root / "utils.py").read_text(), FIXED + "print('hello')\n")

    def test_ask_then_reject(self):
        self.call("/api/ask", {"task": "fix utils.py"})
        self.wait_job()
        self.assertEqual(self.call("/api/reject", {})[1]["status"], "rejected")
        self.assertEqual((self.root / "utils.py").read_text(), ORIGINAL + "print('hello')\n")

    def test_reset_disabled_by_default_then_runs_script(self):
        self.assertEqual(self.call("/api/status")[1]["can_reset"], False)
        self.assertEqual(self.call("/api/reset", {})[0], 409)
        marker = self.root.parent / "reset_ran"
        script = self.root.parent / "reset.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\n")
        script.chmod(0o755)
        Handler.ui.reset_script = script
        self.call("/api/ask", {"task": "fix utils.py"})
        self.wait_job()
        self.assertEqual(self.call("/api/reset", {}), (200, {"reset": True}))
        self.assertTrue(marker.exists())
        self.assertEqual(self.call("/api/job")[1]["status"], "idle")
        self.assertEqual(self.call("/api/approve", {})[0], 409)

    def term(self, cmd):
        return self.call("/api/term", {"cmd": cmd})[1]

    def test_terminal_allowlist(self):
        self.assertIn("ping <host>", self.term("help")["output"])
        self.assertEqual(self.term("ls")["output"], "test_utils.py\nutils.py")
        self.assertIn("def chunk", self.term("cat utils.py")["output"])
        self.assertIn("hello", self.term("python utils.py")["output"])
        for bad in ["rm -rf /", "bash", "ping -f 1.1.1.1", "ping 1.1.1.1; rm x", "ping $(id)",
                    "curl file:///etc/passwd", "curl -o x https://a.b", "python ../x.py", "python -c 1",
                    "cat ../../etc/passwd", "ip link set eth0 down", "ls; id", "'unclosed"]:
            with self.subTest(cmd=bad):
                self.assertEqual(self.term(bad)["status"], "denied")

    def test_terminal_argv_is_fixed(self):
        t = Handler.ui.terminal
        self.assertEqual(t.commands["ping"](["1.1.1.1"]), ["ping", "-c", "2", "-W", "2", "1.1.1.1"])
        self.assertEqual(t.commands["ip"](["route"]), ["ip", "route"])
        self.assertEqual(t.commands["curl"](["https://example.com/x?a=1"])[-1], "https://example.com/x?a=1")

    def test_model_switch(self):
        self.llm.installed = ["fake", "other:1b"]
        status, models = self.call("/api/models")
        self.assertEqual((status, models["available"]), (200, ["fake", "other:1b"]))
        self.assertEqual(self.call("/api/model", {"name": "missing:7b"})[0], 409)
        self.assertEqual(self.call("/api/model", {"name": "other:1b"})[1]["name"], "other:1b")
        for _ in range(100):
            if not self.call("/api/models")[1]["loading"]:
                break
            time.sleep(0.05)
        self.assertEqual((self.llm.loads, self.call("/api/status")[1]["model"]), (["other:1b"], "other:1b"))

    def test_model_switch_refused_while_change_pending(self):
        self.llm.installed = ["fake", "other:1b"]
        self.call("/api/ask", {"task": "fix utils.py"})
        self.assertEqual(self.wait_job()["status"], "pending")
        status, data = self.call("/api/model", {"name": "other:1b"})
        self.assertEqual((status, data["error"]), (409, "approve or reject the proposed change first"))

    def test_question_gets_answer(self):
        self.llm.replies = ["I can see utils.py and test_utils.py."]
        self.llm.intent = "QUESTION"
        self.call("/api/ask", {"task": "what files can you see?"})
        job = self.wait_job()
        self.assertEqual((job["status"], job["answer"]), ("answered", "I can see utils.py and test_utils.py."))


if __name__ == "__main__":
    unittest.main()

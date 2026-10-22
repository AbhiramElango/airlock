"""Run the agent on each eval case with auto-approve and record whether its fix makes the tests pass.

    .venv/bin/python evals/run_eval.py --model qwen3.5:4b --model qwen2.5-coder:3b [--case helper_bug]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airlock.audit import AuditLog  # noqa: E402
from airlock.config import Config, ModelConfig  # noqa: E402
from airlock.llm import OllamaClient  # noqa: E402
from airlock.session import Answer, Session, SessionError  # noqa: E402
from airlock.verify import TestRunner, improves  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
SPEC = "case.json"  # never copied into the repo the agent sees


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def make_repo(case: Path, dest: Path) -> None:
    for f in case.iterdir():
        if f.is_file() and f.name != SPEC:
            shutil.copy(f, dest / f.name)
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "-c", "user.name=eval", "-c", "user.email=eval@localhost", "commit", "-qm", "seed")


def changed_files(diff: str) -> set[str]:
    return {line[6:].rstrip("\t") for line in diff.splitlines() if line.startswith("+++ b/")}


def run_case(case: Path, model: str) -> dict:
    spec = json.loads((case / SPEC).read_text())
    row = {"case": case.name, "model": model, "expect": spec["expect"]}
    with tempfile.TemporaryDirectory() as tmp:
        repo, audit = Path(tmp) / "repo", Path(tmp) / "audit"
        repo.mkdir()
        make_repo(case, repo)
        config = Config(model=replace(ModelConfig(), name=model))
        session = Session(repo, config, OllamaClient(config.model), TestRunner(), AuditLog(audit))
        start = time.monotonic()
        try:
            result = session.propose(spec["task"])
            if isinstance(result, Answer):
                row.update(outcome="answered_only", detail=result.text)
            else:
                after = session.apply(result)
                changed = sorted(changed_files(result.diff))
                # "fixed": the change fixed failing tests and broke none, but the repo has other bugs
                outcome = "passed" if after.passed else "fixed" if improves(result.tests_before, after) else "tests_failed"
                row.update(outcome=outcome, files=changed,
                           right_file=changed in [sorted(e) for e in spec["expect"]],
                           detail=result.explanation, diff=result.diff,
                           self_check="ok" if result.self_check_ok else "failed")
        except SessionError as e:
            row.update(outcome=e.outcome, detail=e.message)
        row["seconds"] = round(time.monotonic() - start)
    return row


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", action="append", help="repeat to compare models (default: config model)")
    p.add_argument("--case", action="append", help="run only these cases")
    args = p.parse_args()
    models = args.model or [ModelConfig.name]
    cases = sorted(c for c in CASES.iterdir() if (c / SPEC).exists() and (not args.case or c.name in args.case))
    out = ROOT / "runs" / f"eval_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    out.parent.mkdir(exist_ok=True)
    rows = []
    for model in models:
        OllamaClient(replace(ModelConfig(), name=model)).warm_up()
        for i, case in enumerate(cases, 1):
            row = run_case(case, model)
            rows.append(row)
            with out.open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(f"[{model} {i}/{len(cases)}] {case.name:<18} {row['outcome']:<14} "
                  f"files={','.join(row.get('files', [])) or '-':<22} check={row.get('self_check') or '-':<7} {row['seconds']:>4}s", flush=True)
    print(f"\n{'model':<18} {'passed':>7} {'right file':>11} {'avg s':>6}")
    for model in models:
        mine = [r for r in rows if r["model"] == model]
        passed = sum(r["outcome"] in ("passed", "fixed") for r in mine)
        right = sum(bool(r.get("right_file")) for r in mine)
        print(f"{model:<18} {passed:>4}/{len(mine)} {right:>8}/{len(mine)} "
              f"{sum(r['seconds'] for r in mine) / len(mine):>6.0f}")
    print(f"details: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Evaluation

`evals/run_eval.py` runs the agent end to end (auto-approve) on small repos with a known bug, each failing its
tests beforehand, and records whether the tests pass after the fix, whether the right files changed, and the time.

```bash
.venv/bin/python evals/run_eval.py [--model TAG ...] [--case NAME ...]   # needs Ollama on the host
```

Results go to `runs/eval_<time>.jsonl` (outcome, changed files, explanation, diff, self-check).

## Cases

| Case | Bug | Right fix |
| --- | --- | --- |
| `bill_single_file` | tip uses 15 instead of 15% | `bill.py` |
| `helper_bug` | symptom in `checkout.py`, bug in the helper it imports | `pricing.py` |
| `missing_argument` | caller passes too few arguments | `report.py` |
| `config_constant` | a percentage used as a fraction | `settings.py` or `invoice.py` |
| `wrong_import` | misspelled import, used twice | `blog.py` |
| `swapped_arguments` | caller swaps a helper's arguments | `billing.py` |
| `rename_field` | field renamed; two callers use the old name | `views.py` + `api.py` |
| `tax_two_callers` | config percentage used as a fraction in two callers | `orders.py` + `invoices.py` |
| `big_file` | one-character bug in a 122-line module | `inventory.py` |
| `misleading_hint` | the user names `report.py`; the bug is in `stats.py` | `stats.py` |
| `restaurant_bill` | demo repo, ask about `bill.py` (another bug exists) | `bill.py` only |
| `restaurant_receipt` | demo repo, "the receipt total is way too high" | `tax.py` only |
| `restaurant_receipt_file` | demo repo, "I think there is something wrong in receipt.py file" | `tax.py` only |

## Results (qwen3.5:4b)

| Design | Passed | Right files |
| --- | --- | --- |
| One file per fix, model rewrites the whole file | 7/10 (two-file bugs failed, 122-line file timed out) | 7/10 |
| SEARCH/REPLACE edits across files | 9/10 | 10/10 |
| + self-check on a scratch copy | 12/12 (avg 71 s) | 12/12 |
| + "fix only what was asked" (current) | **12/13** (avg 84 s) | 13/13 |

- **Self-check** turned `wrong_import` from a miss (import fixed, second use missed) into a pass.
- **Scope rule:** before it, the `restaurant_receipt_file` request also fixed the unrelated `bill.py` bug; now the
  fix stays in `tax.py`. The cost is `rename_field`: the model either renames the field back (the self-check
  catches the broken test) or targets the right callers but miscopies the SEARCH lines.
- **Models** on the first 6 cases: `qwen3.5:4b` 6/6, `qwen2.5-coder:3b` 2/6 (it mostly routed fix requests as
  questions).

Caveat: one run per case. Results vary between runs even at temperature 0, so this shows the design works on
small repos; it is not a benchmark.

# Step 4 · checks

Every check answers one narrow question about one document with `pass`, `fail` or `cannot_tell`. A check never reads another document, and its evidence never quotes a value read on the document.

| File | Role |
|---|---|
| `base.py` | The contract every check follows, and the registry that lists checks by name. |
| `runner.py` | Run every registered check on every document, one document at a time, and write the check table. |
| `settings.py` | Every number the checks use, in one place (environment variables `CHECK_<SETTING>`). |
| `family/` | Step 4: checks by document family, run on the reading from step 2. See its own README. |
| `inspection/` | Checks on the inspection: what the model observed on the pictures, compared with the base form of the type. See its own README. |
| `laptop/` | Step 4: checks that run on the laptop, without Azure, on every document. See its own README. |

The catalogue of all checks, generated from this code, is in `docs/checks.md`.

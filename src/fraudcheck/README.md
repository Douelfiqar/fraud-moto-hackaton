# fraudcheck

The package. One sub-package per step of the pipeline; `cli.py` exposes one command per step.

| File | Role |
|---|---|
| `__main__.py` | Allows `python -m fraudcheck <command>`. |
| `cli.py` | Command line: `python -m fraudcheck ingest/check/decide/evaluate/read/inspect/run`. |
| `config.py` | Shared pieces of every stage's settings: the repository root, `.env` reading, and the loader. |
| `reviewer_app.py` | The reviewer screen: a claims handler sees each decision with its reasons, and has the last word. |
| `checks/` | Step 4: every check, one file per check or per group of checks. See its own README. |
| `decision/` | Steps 5 and 6 of the pipeline: the decision per document, the answer file and the evaluation. See its own README. |
| `demo/` | The control desk: a web page where a claims handler checks a document and has the last word. See its own README. |
| `ingest/` | Step 1 of the pipeline, Ingest: every document file becomes the same record. See its own README. |
| `inspect/` | Step 3, Inspect: one Azure request per document compares it with the base form of its type. See its own README. |
| `read/` | Step 2 of the pipeline, Read: one Azure request per document turns its pictures into named values. See its own README. |

Run `python -m fraudcheck --help` for the commands. The architecture is described in the README at the root of the repository.

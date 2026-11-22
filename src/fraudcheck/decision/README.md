# Steps 5 and 6 · decide and evaluate

Steps 5 and 6 of the pipeline: the decision per document, the answer file and the evaluation.

| File | Role |
|---|---|
| `answer.py` | The answer file, in the organisers' TOML format, checked by reading it back with `tomllib`. |
| `decide.py` | The decision for each document: a recorded decision of the person who validates comes first; otherwise the document is flagged when a trusted check fails, sent to review when it could not be read, and not flagged in every other case. |
| `evaluate.py` | Evaluation on marked examples: fakes caught, wrong flags, the score, and each check's hits. |
| `review.py` | A person's decisions, written one line per document in the review file. |
| `settings.py` | Settings of the decision, the answer file and the evaluation (environment variables `DECIDE_<SETTING>`). |

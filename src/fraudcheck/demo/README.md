# Demo interface

The control desk: a web page where a claims handler checks a document and has the last word.

| File | Role |
|---|---|
| `__main__.py` | Start the control desk and open it in the browser: `python -m fraudcheck.demo`. |
| `analysis.py` | The pipeline's stages, run in order on one document's folder, each one reported as it starts and ends. |
| `app.py` | The HTTP routes of the control desk. |
| `desk.py` | The documents of one session, and what a claims handler does with them. |
| `doc_choices.py` | What the page lists before any upload: the document types by family, and the checks by kind. |
| `workspace.py` | One uploaded document's own folder, outside the repository, laid out like the repository's inputs. |

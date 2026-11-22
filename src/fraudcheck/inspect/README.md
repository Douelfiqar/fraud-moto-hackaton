# Step 3 · inspect

Step 3, Inspect: one Azure request per document compares it with the base form of its type.

| File | Role |
|---|---|
| `inspector.py` | Step 3, Inspect: one Azure request per document, separate from the reading. |
| `models.py` | Shapes of an inspection: the form the model fills, and what is saved for each document. |
| `prompt.py` | The inspection prompt: `prompts/inspect_document.md` filled in for one document. |
| `settings.py` | Settings of the inspection stage (environment variables `INSPECT_<SETTING>`). |
| `store.py` | Where inspections are saved: `cache/<id>/inspection.json`, next to the document's record. |

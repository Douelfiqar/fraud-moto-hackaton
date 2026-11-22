# Step 2 · read

Step 2 of the pipeline, Read: one Azure request per document turns its pictures into named values.

| File | Role |
|---|---|
| `doc_types.py` | Field lists and expected elements per document type, read from `config/doc_types/*.toml`. |
| `gateway.py` | The gateway: the only code of the project that calls Azure OpenAI. |
| `models.py` | Shapes of a reading: the form the model fills, and what is saved for each document. |
| `prompt.py` | The prompt of step 2: read from `prompts/read_document.md` and filled in for one document. |
| `reader.py` | Step 2, Read: one Azure request per document turns its page pictures into named values. |
| `settings.py` | Settings of step 2 (environment variables `READ_<SETTING>`). |
| `store.py` | Where readings are saved: `cache/<id>/reading.json`, next to the record of step 1. |

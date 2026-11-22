# Configuration

Settings that are facts or choices, kept out of the code. Numbers used by the checks are in `src/fraudcheck/checks/settings.py`; secrets are in `.env`, never here.

| File | Role |
|---|---|
| `decision.toml` | Which checks may flag a document, and the confidence written in the answer file. |
| `families.toml` | Which family each document nature belongs to. |
| `licence_numbers.toml` | Length of the driving licence number (item 5 of the European model), country by country. |
| `official_words.toml` | The long words of each official form's own printed wording, by document type. |
| `vehicle_makers.toml` | Maker codes of vehicles (world manufacturer identifier, ISO 3780): the first three characters of a vehicle identification number name its maker. |
| `doc_types/` | One file per type of document: the fields to read and the base form. See its own README. |

# Tests

One file per part of the pipeline. Documents are made by the tests themselves; no test calls the model or the network. Run them with `python -m pytest`.

| File | Role |
|---|---|
| `conftest.py` | Builders for small test files. |
| `test_catalogue.py` | The organisers' spreadsheet and the rules from nature to family. |
| `test_checks_family.py` | Step 4: checks by family on the reading, with hand-made readings only. |
| `test_checks_laptop.py` | Laptop checks: the font check, the check runner and the table, on generated documents only. |
| `test_cli.py` | The command line: summary of counts, exit status, and working from any directory. |
| `test_decision.py` | Steps 5 and 6: decision lines, answer file, evaluation, on generated documents and hand-made results. |
| `test_demo.py` | The control desk (`fraudcheck.demo`): the real stages on an uploaded document, through the HTTP routes. |
| `test_discover.py` | Finding documents: junk files, ids from file names, duplicates. |
| `test_generality.py` | Guards against fitting the solution to one set of documents. |
| `test_generated_size.py` | generated_size: the picture's own size recorded by Ingest, the pixel budget of image generators, and the answer file's genere_ia. |
| `test_inspect.py` | Stage "inspect": base forms, prompt, tiles, references, gateway, replay and the four checks; no network. |
| `test_local_readers.py` | Readers that run on the laptop, both optional: codes (zxing-cpp) and printed words (RapidOCR). |
| `test_pdfs.py` | PDF files: pages drawn into pictures; text, fonts and direction kept for text PDFs. |
| `test_pictures.py` | Picture files: used as they are when plain and small, otherwise a working copy is written. |
| `test_pipeline.py` | The whole step on a small generated folder: records, index, cache reuse and robustness. |
| `test_read.py` | Step 2, Read: field lists, prompt, gateway, replay, orientation; no network, generated documents only. |
| `test_real_set.py` | Integration test on the organisers' documents, when they are present in data/. |
| `test_review.py` | A person's decisions: the review file written line by line, and the reviewer screen driven as a user would. |
| `test_rules_reading.py` | Step 4, rules on the reading: machine-readable lines, licence ages and card dates, plate and VIN, BIC, the RIB fiche's refusals, the European relevé and the bonus-malus history. |
| `test_settings.py` | Settings: defaults, overrides from .env and environment variables, path handling. |
| `test_single_document.py` | Each document is judged on its own: the rule, made structural. |
| `test_typography.py` | Typography inside a text PDF: baseline, size, covered text; generated PDFs only. |

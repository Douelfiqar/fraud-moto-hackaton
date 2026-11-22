# Step 1 · ingest

Step 1 of the pipeline, Ingest: every document file becomes the same record.

| File | Role |
|---|---|
| `barcodes.py` | Barcodes and two-dimensional codes on a page, read on the laptop. |
| `catalogue.py` | The organisers' spreadsheet, which gives each document's nature, and the rules from nature to family. |
| `discover.py` | Find the documents in a folder, skip junk files, and give each document its id. |
| `models.py` | Shapes of what Ingest writes: one record per document, the text layer of text PDFs, and the index. |
| `pdfs.py` | PDF files: draw every page into a picture, and keep the text with its fonts when there is real text. |
| `pictures.py` | Picture files (PNG, JPEG, TIFF, WEBP...): use the file as it is, or write a lighter working copy. |
| `pipeline.py` | Step 1, Ingest: turn every document file into the same record, so later steps never deal with formats. |
| `settings.py` | Every number and path that Ingest uses, in one place. |
| `storage.py` | File helpers: fingerprints, atomic writes, the cache layout and reading saved results back. |
| `words.py` | Words printed on a page, read letter by letter on the laptop. |

"""The control desk: a web page where a claims handler checks a document and has the last word.

``python -m fraudcheck.demo`` starts it and opens the browser. Each uploaded document
goes through the real stages of the pipeline (ingest, read, inspect, checks, decision)
in a temporary folder outside the repository. The page shows each stage as it
finishes, then the decision with its reasons, clues, confidence and every check's
answer; the handler then records their own decision, which replaces the tool's.

Modules:

- ``workspace``: one uploaded document's own folder, and the stage settings pointed at it;
- ``analysis``: the stages, run in order on that folder, each one reported as it ends;
- ``desk``: the documents of the session, the worker that analyses them one at a time,
  the handler's decisions and their journal;
- ``doc_choices``: the document types and the checks, as the page lists them;
- ``app``: the HTTP routes;
- ``static/``: the page itself (HTML, CSS, JavaScript and fonts, nothing downloaded at run time).
"""

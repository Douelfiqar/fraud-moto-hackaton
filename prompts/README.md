# Prompts

The two prompts sent to the model. Each file has a header, then the system part and the user part; the code fills the user part with the fields of the document's type.

| File | Role |
|---|---|
| `read_document.md` | Read: copy the printed values of one document into a strict form, without interpreting them. |
| `inspect_document.md` | Inspect: describe what the page pictures show against the base form of the type (lettering, zones, printed text). |

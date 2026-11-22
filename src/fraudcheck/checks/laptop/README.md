# Checks on the file itself

Step 4: checks that run on the laptop, without Azure, on every document.

| File | Role |
|---|---|
| `_typography.py` | Helpers shared by the typography checks: pieces of text, printed lines, columns, page zones. |
| `baseline_shift.py` | baseline_shift: in one printed line, a piece of text sitting off the line its neighbours sit on. |
| `covered_text.py` | covered_text: text hidden under an opaque shape or picture drawn after it, or text printed on text. |
| `font.py` | font: pieces of a text PDF set in the font an editing tool puts in when the original font is missing. |
| `generated_size.py` | generated_size: a picture whose pixel count is the fixed budget of an image-generation tool, whatever its shape. |
| `official_wording.py` | official_wording: the form's own words are printed with their right letters. |
| `scan_overlay.py` | scan_overlay: nothing typed or drawn on top of a scanned page. |

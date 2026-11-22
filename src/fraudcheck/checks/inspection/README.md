# Checks on what the model saw

Checks on the inspection: what the model observed on the pictures, compared with the base form of the type.

| File | Role |
|---|---|
| `_inspection.py` | Helpers shared by the checks on the inspection: the base form, matching names, French words. |
| `base_form.py` | base_form: every mandatory zone of the base form (photo, signature, machine-readable lines...) is there, in place. |
| `field_order.py` | field_order: the fields come in the order of the base form. |
| `fixed_text_spelling.py` | fixed_text_spelling: the form's own printed text (headings, labels, legal mentions) has no misspelling. |
| `typography_picture.py` | typography_picture: no zone whose lettering clearly differs from its neighbours, as seen on the pictures. |

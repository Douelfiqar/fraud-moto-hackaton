# Checks

Every check answers one narrow question about one document, with `pass`, `fail` or `cannot_tell`, an
evidence sentence and clues. A check never compares two documents, and its evidence never quotes a value
read on the document. This page is generated from the code (`name`, `description`, `source` and `families`
of each check) and from `config/decision.toml`.

There are 45 checks; 27 of them are trusted, which means that a `fail` flags the document.
A failed check that is not trusted is listed for the person who validates, and does not flag.

## What the file itself shows

Computed on the laptop from the file and its page pictures. No model is called.

| Check | Applies to | What it verifies | Basis | Flags | Confidence |
|---|---|---|---|---|---|
| `baseline_shift` | any | Text PDF: a piece of text whose baseline sits off its neighbours' although it has their size. | law, standard or structure of the file | no |  |
| `covered_text` | any | Text PDF: text under an opaque shape or picture drawn after it, or two texts printed on each other. | law, standard or structure of the file | yes | 0.85 |
| `font` | any | Text PDF: pieces set in an editing tool's fallback font, next to the document's own fonts. | law, standard or structure of the file | yes | 0.90 |
| `generated_size` | any | Picture: its pixel count is the fixed budget of an image-generation tool, whatever its shape. | law, standard or structure of the file | yes | 0.90 |
| `official_wording` | any | Official form: none of its own words is printed with wrong letters (read by the local text reader). | law, standard or structure of the file | yes | 0.80 |
| `scan_overlay` | any | Scanned PDF: no visible text, shape or note laid on top of the scan. | law, standard or structure of the file | yes | 0.90 |

## Rules on the values read

Applied to the values the model copied from the document. Each rule compares values of one document with each other, with a law, a standard or a practice sheet.

| Check | Applies to | What it verifies | Basis | Flags | Confidence |
|---|---|---|---|---|---|
| `asylum_counter_days` | identity | Asylum attestation: its date of issue and its date of first registration at the single desk fall on neither a Sunday nor a public holiday. | law, standard or structure of the file | yes | 0.85 |
| `asylum_validity` | identity | Asylum attestation: it runs ten months at most from its date of issue. | law, standard or structure of the file | yes | 0.75 |
| `bic_format` | rib | RIB: the BIC has 8 or 11 characters: bank (4 letters), country (2), place (2), branch (3, optional). | law, standard or structure of the file | no |  |
| `bonus_malus_licence` | releve | Relevé of a car: the coefficient is not lower than the time since the licence printed on it allows (judged only when the relevé says the vehicle is a car). | APRIL practice sheet | yes | 0.75 |
| `bonus_malus_range` | releve | Relevé: the bonus-malus coefficient lies between 0.50 and 3.50. | law, standard or structure of the file | no |  |
| `cg_category_power` | carte_grise | Carte grise: category CL, MTL or MTT1 against engine size and power. | APRIL practice sheet | no |  |
| `cg_mrz_check_digits` | carte_grise | Carte grise: the check digits of the plate, VIN and first registration in the lines are right. | law, standard or structure of the file | yes | 0.80 |
| `cg_mrz_matches_print` | carte_grise | Carte grise: the lines agree with the printed plate, VIN, first registration, make and formula number. | law, standard or structure of the file | yes | 0.80 |
| `cg_plate_format` | carte_grise | Carte grise: the registration number has the current (AA-123-AA) or former (123 ABC 75) format. | law, standard or structure of the file | no |  |
| `cg_vin_format` | carte_grise | Carte grise: the VIN has 17 characters without I, O or Q (vehicles first registered from 2000). | law, standard or structure of the file | no |  |
| `copies_agree` | any | Every copy of a value printed more than once says the same thing (the form it is printed in aside). | law, standard or structure of the file | yes | 0.70 |
| `dates` | any | Every date exists in the calendar, and dates come in a possible order (config/doc_types). | law, standard or structure of the file | yes | 0.80 |
| `foreign_licence_number` | licence | Foreign licence: the licence number has the length its country gives it (countries listed in config/licence_numbers.toml). | law, standard or structure of the file | yes | 0.80 |
| `foreigner_number` | identity | Asylum and extended-processing attestations: the foreigner number has ten digits. | law, standard or structure of the file | yes | 0.80 |
| `iban_check_digits` | rib | RIB: the IBAN's check digits are right (mod 97), after trying the usual misreads. | law, standard or structure of the file | no |  |
| `iban_parts` | rib | RIB: a French IBAN contains the bank code, branch code, account number and key printed apart. | law, standard or structure of the file | no |  |
| `licence_card_dates` | licence | Licence card (model of 2013): issued from 16 September 2013 and valid at most 15 years. | law, standard or structure of the file | yes | 0.85 |
| `licence_categories` | licence | Licence: category A is consistent with A2 and age at the date A was obtained. | law, standard or structure of the file | yes | 0.85 |
| `licence_category_validity` | licence | Licence back: the motorbike and car categories of one card end their validity on the same date. | law, standard or structure of the file | yes | 0.75 |
| `licence_front_categories` | licence | Licence card front: category A is listed with A2 and B with B1, which they include. | law, standard or structure of the file | yes | 0.85 |
| `licence_line_matches_print` | licence | French licence card: the surname in the coded line of the back is the surname printed on the front. | law, standard or structure of the file | no |  |
| `licence_min_age` | licence | Licence: every category was obtained at or above its minimum age (French law). | law, standard or structure of the file | yes | 0.80 |
| `licence_sides_agree` | licence | Licence card scanned on both sides: the categories listed on the front are the ones dated on the back. | law, standard or structure of the file | no |  |
| `mrz_check_digits` | identity | Identity: every check digit of the machine-readable lines is right (ICAO 9303, French card of 1995). | law, standard or structure of the file | no |  |
| `mrz_matches_print` | identity | Identity: the machine-readable lines agree with the printed number, dates and names, in that order. | law, standard or structure of the file | no |  |
| `prolongation_validity` | identity | Attestation of extended processing: it runs three months at most from its date of issue. | law, standard or structure of the file | yes | 0.85 |
| `releve_items` | releve | Relevé: edition date, subscription date, contract status, bonus-malus and vehicle are present. | APRIL practice sheet | yes | 0.70 |
| `releve_new_model` | releve | Relevé: one edited since 24 July 2025 has sections A to F and 'Date et signature' (EU 2024/1855). | APRIL practice sheet | yes | 0.75 |
| `releve_too_old` | releve | Relevé: edited less than three months before the date of the check (APRIL's sheet), and not after it. | APRIL practice sheet | no |  |
| `releve_vehicle_category` | releve | Relevé: the vehicle's category letter (A car, B motorcycle) agrees with the maker named by the identification number printed beside it. | law, standard or structure of the file | yes | 0.85 |
| `rib_key` | rib | RIB: the RIB key matches the bank code, branch code and account number. | law, standard or structure of the file | no |  |
| `rib_refused` | rib | RIB: not a case the fiche refuses (outside the EU, Monaco, savings account, My Pocket). | APRIL practice sheet | no |  |
| `twod_doc_date_matches_print` | any | 2D-Doc: the issuer signed the code around the date of issue printed on the document. | law, standard or structure of the file | yes | 0.90 |
| `twod_doc_matches_print` | any | 2D-Doc: the document's signed code holds the printed surname or a printed first name. | law, standard or structure of the file | yes | 0.90 |
| `validity_expired` | identity, licence | Identity documents, residence permits and licences: the validity date is already past. | APRIL practice sheet | no |  |

## What the model saw on the picture

Applied to the model's description of the page pictures: lettering, missing zones, printed text of the form.

| Check | Applies to | What it verifies | Basis | Flags | Confidence |
|---|---|---|---|---|---|
| `base_form` | any | Inspection: every mandatory zone of the base form is there, in its place (config/doc_types, [base_form]). | law, standard or structure of the file | yes | 0.70 |
| `field_order` | any | Inspection: the fields come in the order of the base form (licence 1 to 9, RIB codes, relevé A to E). | law, standard or structure of the file | no |  |
| `fixed_text_spelling` | any | Inspection: the form's own printed text (headings, labels, legal mentions) has no misspelling. | law, standard or structure of the file | no |  |
| `typography_picture` | any | Inspection: no zone whose lettering clearly differs from its neighbours in font or sharpness, or sits on a background patch. | law, standard or structure of the file | yes | 0.75 |

## Adding a check

A check is one class in `src/fraudcheck/checks/` with a `name`, a `description`, a `source` and a `run` method
that returns a result; `@register` adds it to the registry. It flags documents only once its name is listed
in `config/decision.toml`, with a confidence.

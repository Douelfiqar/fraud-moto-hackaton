# Checks on the values read

Step 4: checks by document family, run on the reading from step 2.

| File | Role |
|---|---|
| `_mrz.py` | Machine-readable lines (MRZ): layouts, check digits and the values they carry. |
| `_values.py` | Helpers shared by the checks on the reading: field access, dates, numbers, normalisation, misreads. |
| `asylum_days.py` | Asylum attestation: the days its two counter dates fall on. |
| `carte_grise.py` | Carte grise: the vehicle category (J.1) against engine size (P.1) and power (P.2). |
| `carte_grise_lines.py` | Carte grise: the two machine-readable lines at the bottom of the certificate. |
| `carte_grise_numbers.py` | Carte grise: the registration number and the vehicle identification number (VIN). |
| `common.py` | Checks for every family: dates exist and come in a possible order; copies of a value agree. |
| `identity.py` | A document whose validity date is already past, at the date of the check. |
| `identity_rules.py` | Paper residence documents: the foreigner number, and how long an asylum attestation runs. |
| `licence.py` | Licence: category A judged by the date it was obtained. |
| `licence_line.py` | licence_line_matches_print: the coded line on the back of a French licence card names its holder. |
| `licence_rules.py` | Licence: the holder's minimum age, and the dates of the card model of 2013. |
| `mrz.py` | Identity documents and residence permits: the machine-readable lines (MRZ). |
| `releve.py` | Relevé d'information: the five items of the fiche, and the bonus-malus bounds. |
| `releve_rules.py` | Relevé: the European model since 24 July 2025. |
| `rib.py` | RIB: IBAN check digits (mod 97), RIB key, and the IBAN against its printed parts. |
| `rib_rules.py` | RIB: the BIC's format, and the cases APRIL's RIB fiche refuses. |
| `twod_doc.py` | twod_doc_matches_print: the document's own 2D-Doc names the person printed on it. |

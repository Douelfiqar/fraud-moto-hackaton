# Document types

One file per type of document. Each gives the names under which the type is filed (`natures`), the fields the model must copy (`fields`), the elements a form of this type shows (`elements`), the order rules between its dates, and the base form used by the inspection (`[base_form]`). Adding a type is adding a file here.

| File | Role |
|---|---|
| `attestation_asile.toml` | Attestation de demande d'asile (family: identity; 9 fields) |
| `attestation_prolongation.toml` | Attestation de prolongation d'instruction d'un titre de séjour (family: identity; 8 fields) |
| `carte_grise.toml` | Carte grise (certificat d'immatriculation) (family: carte_grise; 16 fields) |
| `permis_ancien_recto.toml` | Permis de conduire ancien modèle (papier rose), face avec l'identité (family: licence; 7 fields) |
| `permis_ancien_verso.toml` | Permis de conduire ancien modèle (papier rose), face avec les catégories (family: licence; 6 fields) |
| `permis_etranger.toml` | Permis de conduire étranger (recto ou verso) (family: licence; 15 fields) |
| `permis_nouveau_recto.toml` | Permis de conduire au format carte, recto (family: licence; 16 fields) |
| `permis_nouveau_verso.toml` | Permis de conduire au format carte, verso (family: licence; 8 fields) |
| `piece_identite.toml` | Pièce d'identité (carte nationale d'identité ou passeport) (family: identity; 10 fields) |
| `releve.toml` | Relevé d'information (relevé de sinistres) (family: releve; 17 fields) |
| `rib.toml` | Relevé d'identité bancaire (RIB) (family: rib; 9 fields) |
| `titre_sejour_recto.toml` | Titre de séjour, recto (family: identity; 9 fields) |
| `titre_sejour_verso.toml` | Titre de séjour, verso (family: identity; 4 fields) |

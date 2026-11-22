# team06 · Fraude documentaire Moto

Hackathon IA APRIL × HEC IA, cas 1. Pour chaque justificatif reçu après une souscription (permis, pièce d'identité ou titre de séjour, RIB, relevé d'information, carte grise), le programme dit s'il est faux, pourquoi, et avec quelle confiance. Une personne valide ensuite.

Ce fichier décrit l'architecture et la mise en route. Chaque dossier a son propre `README.md`, qui présente ses fichiers un par un. Le contexte et les règles de travail pour un assistant de code sont dans [`AGENT.md`](AGENT.md).

## Principe

Chaque document est jugé **seul**, par une suite de contrôles étroits. Un contrôle répond à une seule question (« ce PDF contient-il une police de substitution ? », « la clé du RIB est-elle juste ? ») par `pass`, `fail` ou `cannot_tell`, avec une phrase de preuve et des indices. Le modèle de langage lit et décrit ; ce sont des règles écrites en code qui jugent.

## Architecture

```
documents ──▶ 1 ingest ──▶ 2 read ──▶ 3 inspect ──▶ 4 check ──▶ 5 decide ──▶ fichier de réponse
              (local)      (Azure)     (Azure)       (local)     (local)          │
                                                                                  ▼
                                               6 evaluate (mesure)      7 validation par une personne
```

| Étape | Ce qu'elle fait | Modèle |
|---|---|---|
| 1. Préparer (`ingest`) | une image par page ; pour un PDF, son texte avec polices et positions, et ce qui a été posé sur un scan ; deux lecteurs locaux relèvent les codes 2D-Doc et les mots imprimés | aucun |
| 2. Lire (`read`) | une requête par document recopie les valeurs imprimées dans un formulaire strict | gpt-6.1-terra |
| 3. Inspecter (`inspect`) | une requête par document compare l'image au formulaire de base de son type | gpt-6.1-terra |
| 4. Contrôler (`check`) | 45 contrôles : traces d'édition dans le fichier, règles sur les valeurs lues, constats sur l'image | aucun |
| 5. Décider (`decide`) | signale un document quand un contrôle de confiance échoue, applique les décisions enregistrées par la personne qui valide, écrit le fichier de réponse | aucun |
| 6. Mesurer (`evaluate`) | score selon le barème des organisateurs sur des exemples annotés | aucun |
| 7. Valider (écran) | le gestionnaire voit chaque décision avec ses raisons, ses indices et sa confiance, puis la confirme ou la corrige | aucun |

Chaque étape écrit son résultat dans `cache/` et la suivante le relit : une étape se relance seule, et tout ce qui suit la lecture se rejoue sans réseau.

Quatre sortes de preuves, chacune tirée du document jugé et de lui seul :

- **le fichier lui-même**, sans modèle : police de substitution d'un éditeur de PDF, texte caché sous un cache, texte ou formes posés sur un scan, image aux dimensions d'un générateur d'images ;
- **ce que deux lecteurs locaux relèvent sur la page**, sans modèle : le code 2D-Doc signé par l'émetteur, comparé au nom et à la date imprimés ; un libellé officiel imprimé avec des lettres fausses ;
- **des règles sur les valeurs lues** : dates impossibles, clé de RIB, lignes de lecture automatique, modèle européen du relevé, catégorie du véhicule, âges, catégories et durées de validité du permis, longueur du numéro d'un permis étranger, durée légale des attestations de séjour ;
- **ce que le modèle voit sur l'image** : une zone dont la police ou la netteté diffère de ses voisines, une zone obligatoire absente.

Le catalogue des contrôles, avec ce que chacun vérifie et sur quel texte il s'appuie, est dans [`docs/checks.md`](docs/checks.md).

## Choix de conception

- **Précision d'abord.** Un faux positif coûte environ trois faux démasqués. La confiance se donne contrôle par contrôle, dans un seul fichier (`config/decision.toml`). Un contrôle en échec qui n'a pas la confiance est porté sur une liste à relire, sans signaler le document.
- **« Je ne sais pas » est une réponse.** Un champ illisible donne `cannot_tell`, jamais `fail` : une mauvaise photo n'est pas une fraude.
- **Chaque réponse est expliquée.** Le fichier de réponse porte, pour chaque document signalé, le type de fraude, la justification, les indices et la confiance. La justification nomme la règle et le champ, jamais la valeur lue sur le document.
- **Une personne a le dernier mot.** Sa décision, enregistrée avec son motif, remplace la décision automatique ; elle sert aux cas que les règles ne tranchent pas, et le fichier de réponse dit alors qu'une personne a décidé.
- **Aucun document n'est nommé** dans le code, les réglages ou les prompts : une règle décide sur le contenu du document qu'elle juge. Un test le vérifie.
- **Les nombres sont des réglages.** Seuils et tolérances sont dans `src/fraudcheck/checks/settings.py`, chacun avec sa raison ; les listes de faits (constructeurs, pays, mots officiels) sont dans `config/`.

## Usage du modèle et maîtrise de la consommation

- Un seul fournisseur : les déploiements Azure OpenAI de l'équipe. Les contrôles sur le fichier et les deux lecteurs locaux n'appellent aucun modèle.
- Une requête pour lire et une pour inspecter chaque document, avec le déploiement le moins cher. Une relecture n'a lieu que pour une page reçue de travers, ou lorsque le premier modèle renonce à une valeur qu'il a pourtant recopiée ailleurs sur le document : le déploiement le plus fort est alors interrogé une fois.
- Chaque réponse d'Azure est enregistrée : tout le reste (contrôles, décision, mesure, démo) se rejoue sans réseau et sans coût.
- Les réponses sont bornées (`max_completion_tokens`) et structurées (un schéma par requête) ; la partie système du prompt est fixe ; chaque appel est noté dans un journal de tokens (`cache/read/tokens.jsonl`).
- Lire et inspecter un document coûte environ 20 000 tokens en entrée et 1 500 en sortie.

## Installer et lancer

Python 3.11 ou plus.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env    # puis y mettre la clé API ou les identifiants Entra
```

Les documents vont dans `data/documents/documents/` et le tableur des organisateurs dans `data/documents_fraude.xlsx` (jamais dans Git).

```bash
python -m fraudcheck run                      # tout, avec ce qui est déjà enregistré : aucun appel Azure
python -m fraudcheck run --read --inspect     # lit et inspecte d'abord les documents pas encore faits
python -m fraudcheck run --stage final        # fichier de réponse du dépôt final
python -m pytest                              # les tests, sur des documents fabriqués par les tests
```

Chaque étape a aussi sa commande (`ingest`, `read`, `inspect`, `check`, `decide`, `evaluate`) ; `python -m fraudcheck <commande> --help` donne ses options. Le fichier de réponse est écrit dans `cache/answer/`, la liste à relire dans `cache/decision/review.csv`, la table des contrôles dans `cache/checks/table.csv`.

```bash
streamlit run src/fraudcheck/reviewer_app.py   # l'écran de validation
python -m fraudcheck.demo                      # la démo : http://127.0.0.1:8765
```

- **Écran de validation.** Le gestionnaire choisit un document, voit ses pages, la décision de l'outil, sa justification, ses indices, sa confiance et la réponse de chaque contrôle. Il enregistre sa propre décision (faux ou authentique, avec son motif) ; `decide` l'applique.
- **Démo.** Le gestionnaire dépose un justificatif et choisit son type. Le document passe par les vraies étapes, affichées une à une, puis l'écran donne l'avis de l'outil, sa confiance, ses raisons et ses indices. Sans réseau par défaut ; `--azure` autorise la lecture en direct d'un nouveau document.

## Dans ce dépôt

| Chemin | Contenu |
|---|---|
| `src/fraudcheck/` | le code : un sous-dossier par étape (`ingest/`, `read/`, `inspect/`, `checks/`, `decision/`), la ligne de commande (`cli.py`), l'écran de validation et la démo |
| `config/` | la confiance donnée aux contrôles (`decision.toml`), les types de documents (`doc_types/`) et des listes de faits utilisées par les règles |
| `prompts/` | les deux prompts : lire un document, inspecter son image |
| `tests/` | un fichier de tests par étape |
| `docs/` | le catalogue des contrôles (`checks.md`), les règles et le brief des organisateurs |
| `data/`, `cache/` | documents et sorties : en local seulement, ignorés par Git |

Le code, ses commentaires et les README des dossiers sont en anglais ; les textes écrits dans le fichier de réponse sont en français.

## Limites connues

- Les règles et leurs seuils ont été validés sur les documents fournis. Sur des documents jamais vus, la précision n'est pas mesurée.
- Le contrôle sur le lettrage vu par le modèle repose sur un jugement visuel : c'est le premier endroit où chercher un faux positif.
- Quelques réglages viennent de l'observation et non d'un texte. Deux ne peuvent que faire manquer un faux d'une autre origine : la famille de polices de substitution et la taille d'image d'un générateur. Deux autres pourraient se tromper sur un document d'une autre origine : la tolérance de 31 jours entre la signature d'un code 2D-Doc et la date imprimée, et la disposition des lignes codées de la carte grise (une ligne d'une autre longueur donne « je ne sais pas »).
- La règle de validité par catégorie suppose que les catégories moto et voiture d'une même carte partagent une seule validité, ce qu'un pays peut régler autrement.
- La longueur du numéro de permis étranger n'est connue que pour deux pays ; les autres ne sont pas jugés sur ce point.
- Un document expiré n'est pas signalé pour cette seule raison.
- Chaque document est jugé seul : le recto et le verso d'un même permis ne sont pas rapprochés.

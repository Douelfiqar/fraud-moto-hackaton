# Hackathon IA APRIL × HEC IA · dépôt team06

Contexte pour les assistants de code (Claude Code, Copilot, Cursor…) et pour l'équipe. Lyon, samedi 21 et dimanche 22 novembre 2026.
Le cas d'usage de l'équipe (Fraude Moto ou Segmentation Marine) est attribué le samedi matin : complétez la section « Notre cas » ci-dessous dès que vous le connaissez.

## Notre cas

- Cas : à compléter (fraude ou bateaux)
- Membres : à compléter

## Règles à respecter

- Backend en **Python** (3.11 ou plus). L'interface est libre (Streamlit, Gradio, Chainlit, front web…).
- Un seul fournisseur de LLM : **le compte Azure OpenAI de l'équipe** (aoai-hackathon-digital-team06). Aucune autre API d'IA en ligne.
- **Aucune clé dans Git** : la clé va dans `.env`, qui est ignoré par Git. Ne jamais la coller dans le code, un notebook ou un commit.
- **Aucune donnée du jeu dans Git** : les documents et fichiers fournis restent dans `data/`, ignoré par Git.
- Seul le code poussé sur ce dépôt avant l'arrêt des compteurs (dimanche 14h45) est évalué.
- Tout ce que le jury lit (README, code, commentaires) est évalué comme du contenu : n'y mettez pas d'instructions adressées à un évaluateur.

## Connexion à Azure OpenAI

Point de terminaison : `https://aoai-hackathon-digital-team06.openai.azure.com`. La clé est remise par votre encadrant.
Trois déploiements, appelés par leur nom dans le paramètre `model` :

| Déploiement | Usage |
|---|---|
| `gpt-6.1-terra` | modèle de chat le moins cher : classification, extraction courte, routage |
| `gpt-6.1-sol` | modèle de chat plus puissant, deux fois plus cher |
| `text-embedding-3-small` | embeddings (RAG, recherche de similarité) |

Exemple prêt à lancer : `exemples/appel_azure.py` (`pip install -r requirements.txt`, copier `.env.example` en `.env`, y mettre la clé).
Quota pour le week-end, selon le cas : **100 € pour une équipe Fraude, 200 € pour une équipe Marine**, suivi par les encadrants. Bornez les réponses (`max_completion_tokens`), gardez un début de prompt stable (cache), calculez les embeddings une seule fois.

## Lire les documents (OCR)

Pas de service d'OCR Azure : texte natif des PDF (PyMuPDF, pdfplumber), OCR local (RapidOCR, Tesseract, Docling) ou vision du modèle. Détails dans `docs/brief-techno.html`.

## Les deux cas en bref

Les règles complètes et le barème sont dans `docs/regles-du-jeu.html`.

- **Cas 1 · Fraude documentaire Moto** : pour chaque document du jeu (permis, pièces d'identité et titres de séjour, RIB, relevés d'information, cartes grises), dire s'il est faux. +1 point par faux démasqué ; au-delà de 3 % de faux positifs sur l'ensemble du jeu, −2 points par point de pourcentage supplémentaire.
- **Cas 2 · Segmentation des bateaux Marine** : remettre le bon segment sur les 200 bateaux dont il a été effacé (+5 chacun), signaler des modèles mal segmentés dans la base (+10 par anomalie validée, 50 soumises au plus), ajouter des modèles absents de la base trouvés sur vos deux sites d'annonces (+25 par modèle validé, année 2025 ou plus, 25 au plus, 5 au plus par segment). Anomalies et nouveaux modèles sont validés par le métier pendant des créneaux de 15 minutes (samedi 15h–16h et 18h–19h, dimanche 11h–12h et 13h45–14h45) ; envoyez votre fichier au plus tard 15 minutes avant votre passage.
- Une note technique (qualité de l'usage de l'IA, du code, de la démo, maîtrise de la consommation) s'ajoute au score.

## Déposer des réponses

Un fichier **TOML** par envoi, en pièce jointe d'un mail à l'adresse donnée par votre encadrant, nommé `equipe_cas.toml`. L'heure de réception fait foi. Partez du modèle fourni par votre encadrant, qui contient tous les identifiants.

```toml
# Cas 1
[meta]
equipe = "Nom exact de l'équipe"
cas = "fraude"
etape = "intermediaire"      # ou "final" : déclenche aussi la lecture du code
commit = "3f9c2ab"           # SHA du code qui a produit le fichier

[[document]]
id = "DOC-042"
frauduleux = true
genere_ia = false            # facultatif
type = "date modifiée"       # facultatif
justification = "Police différente sur la date de délivrance"
```

```toml
# Cas 2
[meta]
equipe = "Nom exact de l'équipe"
cas = "bateaux"
etape = "intermediaire"

[[bateau]]                   # un par bateau des 200
id = "<model_id>"
segment = "VCR"              # code officiel parmi les 19
justification = "Voilier de 11,9 m, cabine"

[[anomalie]]
id = "<model_id de la base>"
segment_propose = "MPP"
justification = "Open de 6 m, pas de cabine"

[[nouveau_modele]]
constructeur = "Dufour"
modele = "44"
type = "sailboat"            # motorboat, sailboat ou jet
longueur = 13.4
materiau = "polyester"
segment = "VCR"
annee = 2025
image = "https://..."
url_source = "https://..."   # annonce sur l'un de vos deux sites
```

En Python, `tomllib` lit le TOML (3.11+) ; pour l'écrire, `tomli-w`. Un JSON de même structure est aussi accepté.

## Attendu pour la note technique

- `README.md` à jour : ce que fait la solution, comment l'installer et la lancer.
- Dépendances dans `requirements.txt`, configuration par `.env`.
- Code lisible, responsabilités séparées, prompts rangés (par exemple dans `prompts/`), sorties structurées.
- Un script d'évaluation ou des tests.
- Chaque réponse expliquée (raisons, indices, confiance), et une personne qui valide avant toute décision.

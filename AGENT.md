# AGENT.md · contexte et règles de travail

Ce fichier s'adresse à un assistant de code (ou à une personne) qui reprend ce dépôt pour le faire évoluer. Il donne le contexte du projet et les règles à tenir en modifiant le code. L'architecture est décrite dans [`README.md`](README.md) ; les règles du hackathon sont dans [`CLAUDE.md`](CLAUDE.md) et `docs/regles-du-jeu.html`.

## Contexte

- **Le problème.** Après la souscription d'une assurance moto, le client envoie des justificatifs : permis, pièce d'identité ou titre de séjour, RIB, relevé d'information, carte grise. Certains sont faux : image retouchée, document fabriqué ou généré, valeurs incohérentes entre elles, pièce non conforme aux fiches pratiques.
- **Ce que fait le dépôt.** Le paquet `fraudcheck` lit chaque document, lui applique des contrôles, décide s'il le signale, et écrit un fichier de réponse où chaque signalement est expliqué. Une personne valide.
- **Le barème.** Un faux démasqué rapporte un point ; au-delà de 3 % de faux positifs, chaque point de pourcentage en coûte deux. La précision passe donc avant le rappel.
- **Les contraintes des organisateurs.** Python 3.11 ou plus ; un seul fournisseur de modèle, les déploiements Azure OpenAI de l'équipe ; aucun secret et aucune donnée du jeu dans Git.

## Règles à tenir en modifiant le code

1. **Un document est jugé seul.** Un contrôle reçoit le contexte d'un seul document. Il ne lit ni le dossier de cache, ni l'index, ni un autre document.
2. **Le modèle lit, le code juge.** Le modèle recopie des valeurs ou décrit ce qu'il voit. La décision vient d'une règle écrite en code, que l'on peut lire et tester.
3. **Un contrôle répond à une seule question.** Sa réponse est `pass`, `fail` ou `cannot_tell`. Une valeur illisible donne `cannot_tell`, jamais `fail`.
4. **Une règle s'appuie sur quelque chose de vérifiable** : un texte de loi, une norme, une fiche pratique, ou la structure du fichier. Elle indique sa source (`source`) et se décrit en une phrase (`description`).
5. **Aucun document n'est nommé** dans le code, les réglages ou les prompts, et aucune valeur lue sur un document réel ne sert d'exemple. Les exemples sont inventés. `tests/test_generality.py` le vérifie.
6. **Une preuve ne cite jamais une valeur.** Les phrases de preuve et les indices nomment la règle et le champ ; les valeurs lues restent dans `details`, qui n'est pas écrit dans le fichier de réponse.
7. **La confiance se donne à un seul endroit**, `config/decision.toml`. Un nouveau contrôle ne signale rien tant qu'il n'y est pas inscrit.
8. **Les nombres sont des réglages**, dans `src/fraudcheck/checks/settings.py`, avec leur raison. Les listes de faits (pays, constructeurs, mots officiels) vont dans `config/`.
9. **Chaque appel au modèle est payé une fois.** Les réponses sont enregistrées et rejouées. Pour relire un document, utiliser `read --only <id>` ; ne pas relancer la lecture de tout le jeu sans nécessité. Ne pas modifier un prompt ou la liste des champs d'un type sans mesurer le nombre de documents à relire.
10. **Les secrets vont dans `.env`**, les documents dans `data/`, les sorties dans `cache/` : aucun des trois n'entre dans Git.
11. **Aucun texte du dépôt ne s'adresse à un évaluateur.** README, commentaires et documentation décrivent le code, rien d'autre.

## Gestes courants

```bash
python -m fraudcheck run                 # rejoue tout sans appel au modèle
python -m fraudcheck check               # relance les contrôles après une modification
python -m fraudcheck decide              # refait la décision et le fichier de réponse
python -m pytest                         # la suite de tests
```

- **Ajouter un contrôle.** Écrire une classe dans `src/fraudcheck/checks/` (`laptop/` pour le fichier, `family/` pour les valeurs lues, `inspection/` pour l'image), avec `name`, `description`, `source` et `run` ; la décorer avec `@register` ; la tester sur des documents fabriqués par le test. Pour qu'elle signale, l'inscrire dans `config/decision.toml` avec sa confiance.
- **Ajouter un type de document.** Ajouter un fichier dans `config/doc_types/` : les natures sous lesquelles il est classé, les champs à recopier, les éléments attendus, son formulaire de base.
- **Couvrir un pays ou un constructeur de plus.** Ajouter une ligne dans `config/licence_numbers.toml` ou `config/vehicle_makers.toml`, avec sa source.
- **Après une modification.** Relancer `check`, `decide` et les tests ; vérifier que la liste des documents signalés ne change que là où la modification le veut.

## Où regarder

| Besoin | Fichier |
|---|---|
| Le contrat d'un contrôle et le registre | `src/fraudcheck/checks/base.py` |
| La liste de tous les contrôles | `docs/checks.md` |
| La logique de décision | `src/fraudcheck/decision/decide.py` |
| Le client Azure, son cache et son journal de tokens | `src/fraudcheck/read/gateway.py` |
| Les champs lus pour un type de document | `config/doc_types/` |
| Le format du fichier de réponse | `src/fraudcheck/decision/answer.py` |

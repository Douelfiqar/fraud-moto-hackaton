# team06 · Hackathon IA APRIL × HEC IA

Dépôt de l'équipe. À compléter au fil du week-end : c'est ce fichier que le jury lit en premier.

## Ce que fait notre solution

À compléter.

## Installer et lancer

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # puis y mettre la clé API ou les identifiants Entra remis par l'encadrant
python exemples/appel_azure.py
```

## Dans ce dépôt

| Chemin | Contenu |
|---|---|
| `CLAUDE.md` | règles, connexion Azure, formats de dépôt : contexte pour vous et vos assistants de code |
| `docs/regles-du-jeu.html` | règles du jeu et barème des deux cas |
| `docs/brief-techno.html` | brief techno : librairies, OCR, architecture, quota |
| `exemples/appel_azure.py` | premier appel (chat + embeddings) à vos déploiements Azure OpenAI |
| `data/` | données du jeu, à déposer ici en local : jamais poussées dans Git |

"""Premier appel aux déploiements Azure OpenAI de l'équipe (connexion lue dans .env).
Clé API renseignée -> authentification par clé ; sinon identité Entra (client_id + secret)."""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/").removesuffix("/openai/v1")
api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
if api_key:
    print("Mode : clé API")
    client = OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=api_key)
else:
    from azure.identity import ClientSecretCredential, get_bearer_token_provider

    print("Mode : identité Entra")
    credential = ClientSecretCredential(
        os.environ["AZURE_TENANT_ID"],
        os.environ["AZURE_CLIENT_ID"],
        os.environ["AZURE_CLIENT_SECRET"],
    )
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    # Le SDK openai accepte un callable : le jeton Entra est renouvelé automatiquement.
    client = OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=token_provider)

resp = client.chat.completions.create(
    model=os.environ.get("AZURE_OPENAI_MODEL", "gpt-6.1-terra"),  # nom du déploiement
    messages=[{"role": "user", "content": "Réponds en une phrase : à quoi sert un relevé d'information en assurance moto ?"}],
    max_completion_tokens=200,
)
print(resp.choices[0].message.content)
print(resp.usage)  # tokens consommés par l'appel

emb = client.embeddings.create(model=os.environ.get("AZURE_OPENAI_EMBEDDING", "text-embedding-3-small"), input="relevé d'information")
print(len(emb.data[0].embedding), "dimensions")

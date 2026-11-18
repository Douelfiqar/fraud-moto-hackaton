"""Premier appel au compte Azure OpenAI de l'équipe (lit AZURE_OPENAI_* dans .env)."""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url=f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/v1/",
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
)

resp = client.chat.completions.create(
    model=os.environ.get("AZURE_OPENAI_MODEL", "gpt-6.1-terra"),
    messages=[{"role": "user", "content": "Réponds en une phrase : à quoi sert un relevé d'information en assurance moto ?"}],
    max_completion_tokens=200,
)
print(resp.choices[0].message.content)
print(resp.usage)  # tokens consommés par l'appel

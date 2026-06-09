import os
from elasticsearch import AsyncElasticsearch
from dotenv import load_dotenv

load_dotenv()

ES_URL     = os.getenv("ES_URL", "http://localhost:9200")
ES_API_KEY = os.getenv("ES_API_KEY", "")

_client: AsyncElasticsearch | None = None


def get_es() -> AsyncElasticsearch:
    global _client
    if _client is None:
        kwargs = {"hosts": [ES_URL]}
        if ES_API_KEY:
            kwargs["api_key"] = ES_API_KEY
        _client = AsyncElasticsearch(**kwargs)
    return _client


async def close_es():
    global _client
    if _client:
        await _client.close()
        _client = None

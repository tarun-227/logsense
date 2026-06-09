"""
Minimal Elasticsearch MCP server.
Exposes: list_indices, get_mappings, search
Run as: python elastic_mcp_server.py
"""

import json
import os
import sys

from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

load_dotenv()

ES_URL     = os.getenv("ES_URL", "http://localhost:9200")
ES_API_KEY = os.getenv("ES_API_KEY", "")

def get_es():
    if ES_API_KEY:
        return Elasticsearch(ES_URL, api_key=ES_API_KEY)
    return Elasticsearch(ES_URL)

server = Server("elasticsearch-mcp")

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_indices",
            description="List all available Elasticsearch indices",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_mappings",
            description="Get field mappings for a specific Elasticsearch index",
            inputSchema={
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"}
                },
                "required": ["index"],
            },
        ),
        types.Tool(
            name="search",
            description=(
                "Search Elasticsearch using Query DSL. "
                "Pass a full Elasticsearch search body as JSON string."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name or pattern"},
                    "query_body": {
                        "type": "string",
                        "description": "JSON string of the Elasticsearch search body (query, aggs, size, etc.)"
                    },
                },
                "required": ["index", "query_body"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    es = get_es()

    if name == "list_indices":
        resp = es.cat.indices(format="json", h="index,docs.count,store.size,health")
        indices = [
            {"index": r["index"], "docs": r.get("docs.count"), "size": r.get("store.size")}
            for r in resp.body
            if not r["index"].startswith(".")
        ]
        return [types.TextContent(type="text", text=json.dumps(indices, indent=2))]

    elif name == "get_mappings":
        index = arguments["index"]
        resp = es.indices.get_mapping(index=index)
        return [types.TextContent(type="text", text=json.dumps(resp.body, indent=2))]

    elif name == "search":
        index = arguments["index"]
        body  = json.loads(arguments["query_body"])
        resp  = es.search(index=index, **body)
        return [types.TextContent(type="text", text=json.dumps(resp.body, indent=2))]

    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

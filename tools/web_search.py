# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

import os
from crewai.tools import tool
from tavily import TavilyClient

@tool("Web Search")
def web_search_tool(query: str) -> str:
    """Search the web for current information about companies, competitors, market trends, prospects, and industry news. Use this to research before making recommendations."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "ERROR: TAVILY_API_KEY environment variable is not set."
    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=5, search_depth="advanced")
        results = response.get("results", [])
        if not results:
            return f"No results found for query: {query}"
        return "\n\n".join([
            f"Title: {r['title']}\nContent: {r['content'][:1000]}\nSource: {r['url']}"
            for r in results
        ])
    except Exception as e:
        return f"Search error: {type(e).__name__}: {str(e)}"

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
        response = client.search(query, max_results=5)
        results = response.get("results", [])
        if not results:
            return f"No results found for query: {query}"
        return "\n\n".join([
            f"Title: {r['title']}\nContent: {r['content']}\nSource: {r['url']}"
            for r in results
        ])
    except Exception as e:
        return f"Search error: {type(e).__name__}: {str(e)}"

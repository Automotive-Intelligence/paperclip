import os
from crewai.tools import BaseTool
from tavily import TavilyClient

class WebSearchTool(BaseTool):
    name: str = "Web Search"
    description: str = "Search the web for current information about companies, competitors, market trends, prospects, and industry news. Use this to research before making recommendations."

    def _run(self, query: str) -> str:
        try:
            client = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY"))
            response = client.search(query, max_results=5)
            results = response.get("results", [])
            if not results:
                return "No results found."
            return "\n\n".join([
                f"**{r['title']}**\n{r['content']}\nSource: {r['url']}"
                for r in results
            ])
        except Exception as e:
            return f"Search failed: {str(e)}"

web_search_tool = WebSearchTool()

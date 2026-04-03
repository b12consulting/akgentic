"""Tool definitions for the agent team."""

import os

from akgentic.tool.knowledge_graph import KnowledgeGraphTool
from akgentic.tool.mcp import MCPHTTPConnectionConfig, MCPTool
from akgentic.tool.planning import PlanningTool, UpdatePlanning
from akgentic.tool.sandbox import ExecTool
from akgentic.tool.search import SearchTool, WebCrawl, WebFetch, WebSearch
from akgentic.tool.workspace import WorkspaceTool

search_tool = SearchTool(
    web_search=WebSearch(max_results=3),
    web_crawl=WebCrawl(timeout=150, max_depth=3, max_breadth=5, limit=10),
    web_fetch=WebFetch(timeout=30),
)

knowledge_graph_tool = KnowledgeGraphTool()

planning_tool = PlanningTool(
    update_planning=UpdatePlanning(
        instructions="""CRITICAL: Always keep the plan updated.
Create tasks when your task involves other team members
or is complex enough to require multiple steps.
Update task status when you make progress.
Record outputs when you complete work.
Do not finish your turn if the plan is stale."""
    )
)

WORKSPACE_ID = "cli-git"
workspace_tool = WorkspaceTool(workspace_id=WORKSPACE_ID)

# run ./scripts/build-sandbox-iamge.sh to build the sandbox image before using this tool
sandbox_tool = ExecTool(workspace_id=WORKSPACE_ID, mode="auto")

github_tool = MCPTool(
    name="github-mcp-server",
    description="Github MCP server",
    connection=MCPHTTPConnectionConfig(
        url="https://api.githubcopilot.com/mcp/",
        bearer_token=os.getenv("MCP_BEARER_TOKEN", ""),
        transport="streamable-http",
        tool_prefix="github",
    ),
)

tools = [search_tool, workspace_tool, sandbox_tool, planning_tool, knowledge_graph_tool]

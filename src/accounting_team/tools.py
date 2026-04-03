"""Tool definitions for the agent team."""

from akgentic.tool.planning import PlanningTool, UpdatePlanning
from akgentic.tool.sandbox import ExecTool
from akgentic.tool.search import SearchTool, WebCrawl, WebFetch, WebSearch
from akgentic.tool.workspace import WorkspaceTool

search_tool = SearchTool(
    web_search=WebSearch(max_results=3),
    web_crawl=WebCrawl(timeout=150, max_depth=3, max_breadth=5, limit=10),
    web_fetch=WebFetch(timeout=30),
)

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

WORKSPACE_ID = "cli-accounting"
workspace_tool = WorkspaceTool(workspace_id=WORKSPACE_ID)

# run ./scripts/build-sandbox-iamge.sh to build the sandbox image before using this tool
sandbox_tool = ExecTool(workspace_id=WORKSPACE_ID, mode="docker")

tools = [planning_tool, workspace_tool]
    
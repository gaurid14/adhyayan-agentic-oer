from langsmith import traceable

from langgraph_agents.review_graph.review_workflow import compiled_review_graph


@traceable(name="AI Review Pipeline")
async def run_review_pipeline(state: dict):
    return await compiled_review_graph.ainvoke(state)
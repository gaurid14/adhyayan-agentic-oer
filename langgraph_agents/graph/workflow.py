from langgraph.constants import END
from langgraph.graph import StateGraph

from langgraph_agents.agents.clarity import evaluate_clarity
from langgraph_agents.agents.coherence import evaluate_coherence
from langgraph_agents.agents.engagement import evaluate_engagement

# ✅ Wrapper nodes (IMPORTANT)
async def clarity_node(state: dict) -> dict:
    return await evaluate_clarity.ainvoke({"state": state})

async def engagement_node(state: dict) -> dict:
    return await evaluate_engagement.ainvoke({"state": state})

async def coherence_node(state: dict) -> dict:
    return await evaluate_coherence.ainvoke({"state": state})


graph = StateGraph(dict)

# ✅ Add wrapper nodes, not tool directly
graph.add_node("evaluate_clarity", clarity_node)
graph.add_node("evaluate_engagement", engagement_node)
graph.add_node("evaluate_coherence", coherence_node)

graph.set_entry_point("evaluate_clarity")

graph.add_edge("evaluate_clarity", "evaluate_engagement")
graph.add_edge("evaluate_engagement", "evaluate_coherence")
graph.add_edge("evaluate_coherence", END)

compiled_graph = graph.compile()


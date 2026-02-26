from langgraph.graph import StateGraph
from langgraph.constants import END

from langgraph_agents.review_agents.clarity_review import review_clarity
from langgraph_agents.review_agents.engagement_review import review_engagement


# =====================================================
# WRAPPER NODES (IMPORTANT FOR LANGGRAPH)
# =====================================================

async def clarity_node(state: dict) -> dict:
    result = await review_clarity.ainvoke({
        "state": state
    })
    return result


async def engagement_node(state: dict) -> dict:
    result = await review_engagement.ainvoke({
        "state": state
    })
    return result


# =====================================================
# BUILD REVIEW GRAPH
# =====================================================

review_graph = StateGraph(dict)

# Nodes
review_graph.add_node("clarity_review", clarity_node)
review_graph.add_node("engagement_review", engagement_node)

# Entry
review_graph.set_entry_point("clarity_review")

# Flow
review_graph.add_edge("clarity_review", "engagement_review")
review_graph.add_edge("engagement_review", END)

# Compile
compiled_review_graph = review_graph.compile()
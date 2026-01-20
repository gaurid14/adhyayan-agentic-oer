from langgraph.constants import END
from langgraph.graph import StateGraph

from langgraph_agents.agents.clarity import evaluate_clarity
from langgraph_agents.agents.coherence import evaluate_coherence
from langgraph_agents.agents.submission_agent import submission_agent
from langgraph_agents.agents.engagement import evaluate_engagement

from typing import Dict, Any

# State = Dict[str, Any]
# graph = StateGraph(State)

graph = StateGraph(dict)

graph.add_node("submission_agent", submission_agent)
graph.add_node("evaluate_engagement", evaluate_engagement)
graph.add_node("evaluate_clarity", evaluate_clarity)
graph.add_node("evaluate_coherence", evaluate_coherence)

graph.set_entry_point("evaluate_engagement")
graph.add_edge("evaluate_engagement", "evaluate_clarity")
graph.add_edge("evaluate_clarity", "evaluate_coherence")

# graph.add_edge("evaluate_engagement", END)

compiled_graph = graph.compile()


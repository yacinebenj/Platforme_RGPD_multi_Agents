from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from agents.agent_a import run_agent_a
from agents.agent_b import run_agent_b
from agents.agent_c import run_agent_c
from agents.agent_d import run_agent_d

#define the structure of data shared binet el agents
class RGPDState(TypedDict):
    traitement: dict
    incident: Optional[dict]
    demande_dsar: Optional[dict]
    agent_a_output: Optional[dict]
    agent_b_output: Optional[dict]
    agent_c_output: Optional[dict]
    agent_d_output: Optional[dict]
    erreurs: list

#hne nasn3o nodes ta3 kol agent
def node_agent_a(state: RGPDState) -> RGPDState:
    try:
        output = run_agent_a(state["traitement"])
        return {**state, "agent_a_output": output}
    except Exception as e:
        return {**state, "erreurs": state.get("erreurs", []) + ["Agent A: " + str(e)]}


def node_agent_b(state: RGPDState) -> RGPDState:
    try:
        output = run_agent_b(
            state["traitement"],
            state["agent_a_output"],
            state.get("incident")
        )
        return {**state, "agent_b_output": output}
    except Exception as e:
        return {**state, "erreurs": state.get("erreurs", []) + ["Agent B: " + str(e)]}


def node_agent_c(state: RGPDState) -> RGPDState:
    try:
        demande = state.get("demande_dsar")
        if demande:
            output = run_agent_c(demande)
        else:
            output = None
        return {**state, "agent_c_output": output}
    except Exception as e:
        return {**state, "erreurs": state.get("erreurs", []) + ["Agent C: " + str(e)]}


def node_agent_d(state: RGPDState) -> RGPDState:
    try:
        output = run_agent_d(
            state["agent_a_output"],
            state["agent_b_output"],
            state.get("agent_c_output")
        )
        return {**state, "agent_d_output": output}
    except Exception as e:
        return {**state, "erreurs": state.get("erreurs", []) + ["Agent D: " + str(e)]}

#create graph ( agents houma el nodes)
def build_workflow():
    graph = StateGraph(RGPDState)

    graph.add_node("agent_a", node_agent_a)
    graph.add_node("agent_b", node_agent_b)
    graph.add_node("agent_c", node_agent_c)
    graph.add_node("agent_d", node_agent_d)

    graph.set_entry_point("agent_a")
    graph.add_edge("agent_a", "agent_b")
    graph.add_edge("agent_b", "agent_c")
    graph.add_edge("agent_c", "agent_d")
    graph.add_edge("agent_d", END)

    return graph.compile()

#main function
rgpd_workflow = build_workflow()


def run_workflow(traitement: dict, incident: dict = None, demande_dsar: dict = None) -> dict:
    initial_state = RGPDState(
        traitement=traitement,
        incident=incident,
        demande_dsar=demande_dsar,
        agent_a_output=None,
        agent_b_output=None,
        agent_c_output=None,
        agent_d_output=None,
        erreurs=[]
    )

    final_state = rgpd_workflow.invoke(initial_state)

    return {
        "statut": "succes" if not final_state.get("erreurs") else "partiel",
        "erreurs": final_state.get("erreurs", []),
        "agent_a": final_state.get("agent_a_output"),
        "agent_b": final_state.get("agent_b_output"),
        "agent_c": final_state.get("agent_c_output"),
        "agent_d": final_state.get("agent_d_output")
    }

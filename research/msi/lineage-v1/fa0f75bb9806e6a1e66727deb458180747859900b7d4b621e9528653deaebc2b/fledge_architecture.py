"""Structural measurements for future stationary breeding. Does not change RNG draws."""
from collections import deque
from statistics import mean

from wisp.core import Genome, MAX_EDGES, MAX_NODES, MOTORS, SENSORS


def distances(start, adjacency):
    found = {node: 0 for node in start}
    remaining = deque(start)
    while remaining:
        node = remaining.popleft()
        for target in adjacency.get(node, ()):
            if target not in found:
                found[target] = found[node] + 1
                remaining.append(target)
    return found


def topology(genome):
    """Directed inherited wiring, without claims about activation or causal contribution."""
    incoming, outgoing = {}, {}
    for edge in genome.edges:
        outgoing.setdefault(edge.source, []).append(edge.target)
        incoming.setdefault(edge.target, []).append(edge.source)
    forward = distances(SENSORS, outgoing)
    backward = distances(MOTORS, incoming)
    connected = {n.id for n in genome.nodes if n.id in forward and n.id in backward}
    shortest = [distances([sensor], outgoing).get(motor) for sensor in SENSORS for motor in MOTORS]
    return dict(neurons=len(genome.nodes), connections=len(genome.edges),
                sensor_to_motor_nodes=len(connected), sensor_to_motor_interneurons=len(connected-set(MOTORS)),
                connected_sensor_motor_pairs=sum(n is not None for n in shortest),
                shortest_edges_by_sensor_motor=shortest)


def measure_generation(members, summary, children, wiring, rank):
    sample = {(m["group"], m["slot"]): Genome.from_dict(m["genome"]) for m in members}
    ordered = sorted(summary["members"], key=rank, reverse=True)
    if len(sample) != len(ordered) or set(sample) != {(r["group"],r["slot"]) for r in ordered}:
        raise ValueError("Architecture sample differs from the scored cohort")
    rows = [dict(group=r["group"], slot=r["slot"], **topology(sample[r["group"], r["slot"]])) for r in ordered]
    # In core._change_wiring add_node and duplicate_node are removed BEFORE random choice at the cap.
    # Thus no selected proposal is rejected. Count actual parent wiring draws whose menus lost growth.
    out = dict(wiring_mutations=0, neuron_cap_filtered_draws=0, edge_cap_filtered_draws=0,
               both_caps_filtered_draws=0, node_growth_menu_entries_removed_by_neuron_cap_alone=0,
               observed_node_gains=0, rejected_selected_growth_attempts=0)
    for child in children or ():
        if child["birth_generation"] != summary["generation"] + 1 or not wiring[child["group"]]:
            continue
        parent = sample[child["group"], child["parent_slot"]]
        at_node, at_edge = len(parent.nodes) >= MAX_NODES, len(parent.edges) >= MAX_EDGES
        out["wiring_mutations"] += 1
        out["neuron_cap_filtered_draws"] += int(at_node)
        out["edge_cap_filtered_draws"] += int(at_edge)
        out["both_caps_filtered_draws"] += int(at_node and at_edge)
        if at_node and not at_edge:
            out["node_growth_menu_entries_removed_by_neuron_cap_alone"] += 1 + int(any(n.id not in MOTORS for n in parent.nodes))
        out["observed_node_gains"] += int(len(child["genome"]["nodes"]) > len(parent.nodes))
    return dict(generation=summary["generation"], count=len(rows), neuron_cap=MAX_NODES, edge_cap=MAX_EDGES,
                maximum_neurons=max(r["neurons"] for r in rows), at_neuron_cap=sum(r["neurons"] == MAX_NODES for r in rows),
                mean_sensor_to_motor_nodes=mean(r["sensor_to_motor_nodes"] for r in rows),
                top_by_training_rank=rows[:4], cohort=rows, breeding_to_next=out if children is not None else None)


def generation(members, generation_index):
    """Drop-in for future users of the stationary selection protocol."""
    import fledge_blank_breeding as study
    children, summary, nominee = study.generation(members, generation_index)
    wiring = {group: flags[1] for group, flags in study.CONDITIONS.items()}
    summary["architecture"] = measure_generation(members, summary, children, wiring, study.training_rank)
    return children, summary, nominee

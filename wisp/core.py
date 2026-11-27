"""A small evolvable recurrent network with reward-modulated motor synapses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
import random


SENSORS = (-1, -2)
MOTORS = (0, 1)
MAX_NODES = 12
PILOT_MAX_NODES = 24
MAX_EDGES = 64
WEIGHT_LIMIT = 3.0
MAX_TRACE_DECAY = .95


def _check_node_limit(node_limit):
    if type(node_limit) is not int or not MAX_NODES <= node_limit <= PILOT_MAX_NODES:
        raise ValueError("Choose a neuron ceiling from 12 to 24.")


def _bound(value, low, high):
    return max(low, min(high, value))


@dataclass(frozen=True)
class Neuron:
    id: int
    leak: float
    gain: float
    adaptation: float


@dataclass(frozen=True)
class Connection:
    source: int
    target: int
    weight: float


@dataclass(frozen=True)
class Genome:
    nodes: tuple[Neuron, ...]
    edges: tuple[Connection, ...]
    learning_rate: float
    trace_decay: float = 0.0

    def to_dict(self) -> dict:
        data = {
            "nodes": [asdict(node) for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
            "learning_rate": self.learning_rate,
        }
        if self.trace_decay:
            data["trace_decay"] = self.trace_decay
        return data

    @classmethod
    def from_dict(cls, data: dict, *, node_limit=MAX_NODES) -> Genome:
        _check_node_limit(node_limit)
        decay = data.get("trace_decay", 0.0)
        if type(decay) not in (int, float) or not 0 <= decay <= MAX_TRACE_DECAY:
            raise ValueError("invalid eligibility trace decay")
        genome = cls(
            tuple(Neuron(**item) for item in data["nodes"]),
            tuple(Connection(**item) for item in data["edges"]),
            float(data["learning_rate"]),
            float(decay),
        )
        ids = {node.id for node in genome.nodes}
        pairs = {(edge.source, edge.target) for edge in genome.edges}
        if (len(ids) != len(genome.nodes) or len(ids) > node_limit
                or not set(MOTORS) <= ids or any(i < 0 for i in ids)):
            raise ValueError("invalid neuron IDs or network size")
        if (len(pairs) != len(genome.edges) or len(pairs) > MAX_EDGES
                or any(src not in ids.union(SENSORS) or dst not in ids for src, dst in pairs)):
            raise ValueError("invalid or duplicate connection")
        if any(not (0 < node.leak <= 1 and 0 < node.gain <= 4 and 0 <= node.adaptation <= 1)
               for node in genome.nodes):
            raise ValueError("invalid neuron parameters")
        if (not 0 <= genome.learning_rate <= 1
                or any(not -WEIGHT_LIMIT <= edge.weight <= WEIGHT_LIMIT for edge in genome.edges)):
            raise ValueError("invalid learning rate or synapse weight")
        return genome


def random_genome(rng: random.Random) -> Genome:
    # Exact zero selects the simple leaky rule. Each founder starts with both rules.
    adaptive = rng.sample(range(4), 2)
    nodes = tuple(Neuron(i, rng.uniform(.55, .9), rng.uniform(.8, 1.25),
                         rng.uniform(.02, .22) if i in adaptive else 0.0) for i in range(4))
    # Both motors start reachable from either receptor; the weights encode no cue rule.
    pairs = [(src, dst) for src in SENSORS for dst in range(4)]
    pairs.extend([(2, 0), (3, 1), (2, 3), (3, 2)])
    edges = tuple(Connection(src, dst, rng.uniform(-.65, .65)) for src, dst in pairs)
    return Genome(nodes, edges, rng.uniform(.24, .5))


def _change_wiring(nodes, edges, rng, node_limit):
    ids = [node.id for node in nodes]
    hidden = [node for node in nodes if node.id not in MOTORS]
    pairs = {(edge.source, edge.target) for edge in edges}
    available = [(src, dst) for src in [*SENSORS, *ids] for dst in ids
                 if (src, dst) not in pairs]
    operations = []
    if available and len(edges) < MAX_EDGES:
        operations.append("add_edge")
    if edges:
        operations.append("delete_edge")
    if hidden:
        operations.append("delete_node")
    if len(nodes) < node_limit and len(edges) < MAX_EDGES:
        operations.append("add_node")
        if hidden:
            operations.append("duplicate_node")

    operation = rng.choice(operations)
    if operation == "add_edge":
        src, dst = rng.choice(available)
        edges.append(Connection(src, dst, rng.uniform(-.6, .6)))
    elif operation == "delete_edge":
        edges.pop(rng.randrange(len(edges)))
    elif operation == "delete_node":
        removed = rng.choice(hidden)
        nodes.remove(removed)
        edges[:] = [edge for edge in edges if removed.id not in (edge.source, edge.target)]
    else:
        new_id = next(i for i in range(node_limit) if i not in ids)
        template = rng.choice(hidden or nodes)
        nodes.append(replace(template, id=new_id))
        if operation == "duplicate_node":
            copied = [replace(edge,
                              source=new_id if edge.source == template.id else edge.source,
                              target=new_id if edge.target == template.id else edge.target)
                      for edge in edges if template.id in (edge.source, edge.target)]
            edges.extend(copied[:MAX_EDGES - len(edges)])
        else:
            previous = (edges.pop(rng.randrange(len(edges))) if edges
                        else Connection(rng.choice(SENSORS), rng.choice(MOTORS), .5))
            edges.extend([Connection(previous.source, new_id, 1.0),
                          Connection(new_id, previous.target, previous.weight)])


def mutate(genome: Genome, rng: random.Random, *, rules: bool, wiring: bool,
           evolve_trace: bool = False, node_limit=MAX_NODES) -> Genome:
    """Create an offspring from inherited parameters, never from lifetime weights."""
    _check_node_limit(node_limit)
    if len(genome.nodes) > node_limit:
        raise ValueError("Parent exceeds the selected neuron ceiling.")
    nodes, edges = list(genome.nodes), list(genome.edges)
    if wiring:
        _change_wiring(nodes, edges, rng, node_limit)
    # Preserve the post-wiring neuron list if this offspring later spends its
    # single rule mutation on trace decay. Other mutation draws stay matched.
    before_rule = nodes.copy() if rules and evolve_trace else None
    if rules:
        index = rng.randrange(len(nodes))
        node = nodes[index]
        if rng.randrange(4) == 0:
            adaptation = 0.0 if node.adaptation else rng.uniform(.02, .22)
            nodes[index] = replace(node, adaptation=adaptation)
        else:
            parameters = [("leak", .1, 1, .13), ("gain", .25, 2.5, .22)]
            if node.adaptation:
                parameters.append(("adaptation", .01, .8, .09))
            parameter, low, high, spread = rng.choice(parameters)
            new_value = _bound(getattr(node, parameter) + rng.gauss(0, spread), low, high)
            nodes[index] = replace(node, **{parameter: new_value})
    if edges:
        index = rng.randrange(len(edges))
        edge = edges[index]
        weight = _bound(edge.weight + rng.gauss(0, .26), -WEIGHT_LIMIT, WEIGHT_LIMIT)
        edges[index] = replace(edge, weight=weight)
    rate = _bound(genome.learning_rate * math.exp(rng.gauss(0, .17)), .01, .95)
    decay = genome.trace_decay
    if before_rule is not None and rng.random() < .5:
        nodes = before_rule
        if not decay:
            decay = rng.uniform(.20, MAX_TRACE_DECAY)
        else:
            decay = _bound(decay + rng.gauss(0, .20), 0.0, MAX_TRACE_DECAY)
    return Genome(tuple(nodes), tuple(edges), rate, decay)


class Wisp:
    """An individual; choices, traces, learned weights and random stream are resumable."""

    def __init__(self, genome: Genome, seed: int = 0, *, plastic: bool = True):
        self.genome = genome
        self.plastic = plastic
        self.trials = 0
        self.last_cue: int | None = None
        self.last_action: int | None = None
        self._rng = random.Random(seed)
        self._indices = {node.id: i for i, node in enumerate(genome.nodes)}
        self._outputs = [self._indices[motor] for motor in MOTORS]
        self._weights = [edge.weight for edge in genome.edges]
        self._activation = [0.0] * len(genome.nodes)
        self._adaptation = [0.0] * len(genome.nodes)
        self._eligibility = [0.0] * len(genome.edges)
        self._pending = False

    @property
    def pending(self) -> bool:
        return self._pending

    def choose(self, cue: int) -> int:
        if cue not in (0, 1):
            raise ValueError("cue must be 0 or 1")
        receptors = {SENSORS[0]: float(cue == 0), SENSORS[1]: float(cue == 1)}
        # Two synchronous ticks allow a sensory signal to propagate through one interneuron.
        for _ in range(2):
            current = [0.0] * len(self.genome.nodes)
            presynaptic = []
            for edge, weight in zip(self.genome.edges, self._weights):
                source = (receptors[edge.source] if edge.source < 0
                          else self._activation[self._indices[edge.source]])
                presynaptic.append(source)
                current[self._indices[edge.target]] += weight * source
            for i, node in enumerate(self.genome.nodes):
                response = math.tanh(node.gain * current[i] - node.adaptation * self._adaptation[i])
                self._activation[i] += node.leak * (response - self._activation[i])
                if node.adaptation:
                    self._adaptation[i] += .15 * (self._activation[i] - self._adaptation[i])
        delta = self._activation[self._outputs[1]] - self._activation[self._outputs[0]]
        probability_one = 1 / (1 + math.exp(-2.2 * delta))
        action = int(self._rng.random() < probability_one)
        # An ungraded action contributes its own signed pre/post trace. An
        # inherited decay keeps part of earlier actions' eligibility until grading.
        decay = self.genome.trace_decay
        previous = self._eligibility
        self._eligibility = [0.0] * len(self.genome.edges)
        for i, edge in enumerate(self.genome.edges):
            if edge.target in MOTORS:
                direction = 1 if edge.target == action else -1
                self._eligibility[i] = direction * presynaptic[i]
                if decay:
                    self._eligibility[i] += decay * previous[i]
        self._pending = True
        self.last_cue = cue
        self.last_action = action
        return action

    def feedback(self, value: int) -> None:
        if value not in (-1, 1):
            raise ValueError("feedback must be -1 or +1")
        if not self._pending:
            raise ValueError("choose before giving feedback")
        if self.plastic:
            # Local decay on active synapses allows old associations to reverse.
            for i, trace in enumerate(self._eligibility):
                weight = self._weights[i]
                change = value * trace - .4 * abs(trace) * weight
                self._weights[i] = _bound(weight + self.genome.learning_rate * change,
                                          -WEIGHT_LIMIT, WEIGHT_LIMIT)
        if self.genome.trace_decay:
            self._eligibility = [0.0] * len(self._eligibility)
        self._pending = False
        self.trials += 1

    def checkpoint(self) -> dict:
        version, state, gaussian = self._rng.getstate()
        return {
            "version": 2 if self.genome.trace_decay else 1, "genome": self.genome.to_dict(), "plastic": self.plastic,
            "trials": self.trials, "rng": [version, list(state), gaussian],
            "weights": self._weights.copy(), "activation": self._activation.copy(),
            "adaptation": self._adaptation.copy(), "eligibility": self._eligibility.copy(),
            "pending": self._pending, "last_cue": self.last_cue, "last_action": self.last_action,
        }

    @classmethod
    def restore(cls, data: dict) -> Wisp:
        if data["version"] not in (1, 2):
            raise ValueError("unsupported Wisp checkpoint version")
        wisp = cls(Genome.from_dict(data["genome"]), plastic=data["plastic"])
        if (data["version"] == 2) != bool(wisp.genome.trace_decay):
            raise ValueError("Wisp checkpoint version does not match its eligibility rule")
        for name in ("weights", "activation", "adaptation", "eligibility"):
            values = list(data[name])
            if len(values) != len(getattr(wisp, "_" + name)) or not all(map(math.isfinite, values)):
                raise ValueError(f"invalid checkpoint {name}")
            setattr(wisp, "_" + name, values)
        version, state, gaussian = data["rng"]
        wisp._rng.setstate((version, tuple(state), gaussian))
        wisp._pending = data["pending"]
        wisp.trials = data["trials"]
        wisp.last_cue = data["last_cue"]
        wisp.last_action = data["last_action"]
        return wisp

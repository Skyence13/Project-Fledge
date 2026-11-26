# Project Fledge first Wisp implementation brief

26 November 2026 · Implementation target [Skyence13/Project-Fledge](https://github.com/Skyence13/Project-Fledge)

## First assignment

Build the virtual evolution laboratory and demonstrate one Wisp learning a rule that was assigned after it was instantiated. This brief replaces the first-assignment ordering in the earlier design and mind research documents. The head-movement prediction, natural object teaching and physical handover experiments follow this first demonstration.

**A Wisp is one instantiated mind.** Give it an individual ID, a genome ID, a lineage, a body description and a resumable lifetime checkpoint. The genome specifies neuron types and their update rules, wiring, initial weights, and permitted plasticity rules. Offspring begin with fresh lifetime state. Resuming an individual restores its learned connections and memory. Preserve a selected individual while evolution proceeds on other instances.

## What Codex should build first

1. **One virtual arena.** Use two distinguishable patterns on two light sensors, two actions (turn left or right), and a time-stamped teaching signal delivered after a choice. Keep the correct assignment and evaluator state outside the Wisp. A visible real-time mode lets Morgan teach or observe one individual. An accelerated mode runs the same sensor and action contract using simulated time.
2. **One neural framework with evolvable neurons.** From generation one, allow mutations in both the neuron’s internal update rule and the circuit wiring. Start each lineage small. Use a shared interpreter with a bounded set of primitives for input integration, leak, activation and adaptation. Start with leaky units and units with an additional adaptive-threshold state. A genome can vary the rule form, time constants, gain, type assignment, and parameters of activity- or feedback-gated synaptic plasticity. Permit addition, deletion or duplication of units and connections, including recurrent connections, within explicit limits on state, wiring and compute. Keep inheritance, mutation and lifetime updates separately observable.
3. **One lifetime-learning experiment.** Counterbalance the cue-to-action assignment across fresh lifetimes. Supply a fixed budget of teaching trials. Then remove feedback and score the first response to each cue on balanced, shuffled trials. In a separate block, reverse the assignment and score how many teaching interactions are needed to adapt. Include an opportunity to test retention after an intervening task.
4. **A credible comparison.** Run a simple cue–action association learner and a fixed reflex on the same observations and teaching budget. Run Wisp variants with shuffled feedback and with synaptic updates disabled; record remaining adaptation from recurrent activity. The cue learner is the initial comparison. Retain the original design’s fuller exemplar learner for later naming and cross-body tests. Report the effect of each mechanism and the search compute expended.
5. **An experiment runner.** Checkpoint populations and individual lifetimes, record seeds and configuration, and preserve several promising lineages. Compare independent runs on held-out sequences. Benchmark completed lifetimes per wall-clock hour at increasing worker counts on the actual lab PC. Keep the physics step, neural update interval and feedback delay fixed in simulated time.

**First deliverable.** One command opens the virtual arena for live teaching. A second runs a small repeatable population evaluation and produces learning curves, control results, runtime and a replayable individual checkpoint. Include four conditions crossing fixed or evolvable neuron rules with fixed or evolvable wiring. All four have the same access to lifetime learning. Match teaching and search budgets, record computational cost, and compare multiple independent lineages on unseen cue sequences. Verify that a restart preserves a learned association and that a fresh offspring learns its own assignment.

## Conserving useful designs

Select a repertoire of candidate neuron types on several tasks, including reversal, delayed feedback and retention, rather than declaring a universal optimum on the two-light task. Fork matched descendants into three conditions: ordinary mutation, reduced mutation of selected neuron-rule genes, and frozen copies of those genes. Keep circuit wiring evolvable in this comparison. Preserve the ability of every individual to learn through permitted synaptic changes. Evaluate new tasks and older lessons side by side, recording learning speed, interference, resource use and variation across runs. Reopen a lineage from a saved ancestor if a new challenge exposes a limitation.

## How specialized circuits can develop

Let mutations duplicate a connected group, alter its inputs and outputs, and allow the copy to diverge while the original keeps functioning. Keep a comparison lineage with the same resources and ordinary node/edge mutations. In a later comparison, seed a lightly connected set of interacting groups using the same neural primitives and compare it with unconstrained wiring. Tasks should recombine demands such as orientation, feedback learning, motor recalibration, distraction and delayed recall; reward performance on those tasks.

Use anatomical names as working analogies for circuit functions. A measured motion error could train a cerebellum-inspired circuit. Successful recall of an association in its original context could suggest a hippocampus-inspired circuit. Test candidate roles by briefly silencing a group on a copy of a Wisp, checking for selective behavioural impairment, and restoring it. Compare against silencing random groups matched for size and activity. Record which structure was supplied by the designers, which arose through mutation, and which function the experiment supports. A distributed solution remains eligible.

Comparative biology offers precedents for shared brain architecture, specialization and divergence. The vertebrate forebrain, midbrain and hindbrain are ancient divisions, while the six-layered neocortex is a mammalian specialization. These comparisons inform possible experiments without prescribing an anatomical timetable for Wisps. [Comparative review](https://pmc.ncbi.nlm.nih.gov/articles/PMC7618898/) · [Vertebrate pallium review](https://doi.org/10.1146/annurev-cellbio-120319-112654) · [Evolution of modular networks in simulation](https://pubmed.ncbi.nlm.nih.gov/16174729/)

## Readiness for a physical avatar

Let the population develop in Godot. Advance on observed capabilities. Set numerical promotion thresholds and a teaching budget after a development pilot, then freeze them before evaluating finalists on fresh trials.

| Evidence to seek | How to check it |
| --- | --- |
| Teachability | Morgan chooses a fresh cue or object name on a frozen build. The Wisp learns from a practical number of interactions, accepts a correction, and retains an unrelated lesson. |
| Continuity | The same individual recalls the lesson after a restart and after an intervening task. Its checkpoint includes learned synapses, relevant dynamic state, body calibration and history. |
| Body adaptation | Change sensor gain, viewpoint, noise or action delay in a second simulated body. Measure whether the Wisp recovers motor performance while retaining the taught association. |
| A bounded first physical task | Choose a task the candidate already performs in simulation. Start with a sensor-only bench or a stationary head. Verify movement limits, command expiry and local stopping before enabling an actuator. |

The first live transfer trial is bidirectional. Teach a cue or object in Godot, ask for the response at the bench, then teach a second association at the bench and test it back in Godot. Record the first unprompted responses in each direction. A virtual candidate earns the opportunity to take this test; its result establishes what actually transferred.

**Decisions intentionally deferred.** Choose population size and concurrency from measured throughput. Set mutation rates and conservation criteria from development runs before testing on held-out tasks. Select cameras, actuators, chassis and appearance when the first physical task is known. Evolution may continue after an individual moves into a physical body.

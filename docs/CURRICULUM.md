# Reviewing a Wisp generation and choosing its next lesson

## What the laboratory does today

The two-cue curriculum has six stages per lifetime. A candidate receives eight baseline probes, 16 teaching trials, eight recall probes, eight probes immediately after the rule reverses, 16 reteaching trials and eight reversal-recall probes. A second fresh lifetime uses the complementary assignment. One generation therefore takes 128 cohort trials. Every Wisp sees the same number of opportunities.

The lab archives the evaluated population at that boundary. With auto-advance off, it holds the generation for review. In automatic training with auto-advance on, it breeds fresh offspring and repeats the protocol with the next seeded lesson. This can continue for many generations. New curricula enter the lab as reviewed changes to the experiment. The personal Wisp has its own lifetime.

## What merits a new lesson

Morgan and the project maintainer review the evidence together. Codex can read a live report, inspect an export, run held-out comparisons, and implement the agreed next experiment. Record the curriculum revision, source revision, training budget and seeds. Keep a checkpoint on the original task so an apparent improvement on a new task can be checked for loss of an old skill.

Use development trials to pick a practical teaching budget and a proficiency criterion. Freeze both before opening the evaluation results. One **proposed first review**, subject to the pilot, would use five independently seeded populations. Select the finalist from each using training data alone. Evaluate each finalist in 32 pairs of fresh lifetimes with opposing cue assignments and 16 feedback events per teaching block. As an example decision rule, look for at least 90% accuracy on *each* of immediate recall and reversal recall in at least four of five lineages. Report the numbers separately, the first unprompted responses, variation across lineages, and the matched reflex, disabled-plasticity, uninformative-feedback and direct-association controls. Trials from the same lineage share a history. Hundreds of such responses still represent one evolutionary replicate.

The current deterministic two-choice lesson has a simple association solution that reaches 100%. When the lesson establishes teachability and stops distinguishing architectures, a harder diagnostic task becomes informative. A poor result can also motivate a new diagnostic if it isolates a suspected limitation. Generation count alone supplies neither result.

## Memory experiments available in the lab

The second curriculum, `wisp-delay-retention-v2`, is a bounded evaluation of an archived population. Each inherited genome starts fresh lifetimes and learns within them. The original population can stay paused. Reproductive selection still uses the version-one curriculum. Select **Memory experiments** in Godot or run `python -m wisp.memory_experiment --help` for batch evaluation of specific generations and founder seeds.

| Experiment | Teaching and probes | Measurement |
| --- | --- | --- |
| Delayed feedback | Teach both cues eight times each. Deliver each signed grade after zero, one, or three ungraded choices on other stimuli. The grade applies to the original choice. | First choice and four ungraded recall probes per cue. Inspect the opposite-target subset to see whether the Wisp can still distinguish cues that require different responses. |
| Retention after interference | Teach A, probe A, then present cue B eight times. One copy receives feedback on B and the matched copy sees B without feedback. Probe A first, then B and A again. | Compare the first A response between copies and check whether B was actually learned. The two cues can have the same or opposite targets. |
| Body adaptation, later | Change a simulated sensor gain, viewpoint or actuation delay while preserving a previously learned association. | Trials to recover motor accuracy and retention of the association. This establishes a virtual comparison before testing a physical avatar. |

The four possible assignments of left and right choices are equally represented, and either cue can play the role of A. The Wisp receives a cue and a signed grade. The association reference knows the scheduled feedback delay and can look up its own earlier choice. The fixed reflex, disabled-plasticity Wisp and Wisp receiving unrelated feedback establish comparison scores. The first response contributes to the four-probe score. Pooled counts include repeated trials on the same genomes. A change across delays measures performance with intervening stimuli and choices, rather than decay over elapsed seconds.

Before enabling reproductive selection on a harder curriculum, compare early and late saved populations on the same sequences across independent founder seeds. Choose the training objective, budget and retention criterion on development runs, reserve separate evaluation seeds, and keep the original reversal test as a check for lost skills. A lineage fork currently resumes the same curriculum with a new mutation stream.

## When to experiment with brain structures

A founder has four recurrent-network units including two designated motor units. Wiring-enabled descendants can add or remove connections, add or delete hidden units, or duplicate a hidden unit and part of its connectivity. Current limits are 12 units and 64 connections per genome. Rule-enabled descendants can change leaky/adaptive dynamics. During a lifetime, the current eligibility rule changes synapses feeding the motor units. Those mechanisms already allow circuits to differ. Duplicating an entire connected group, assigning anatomical regions, and experimentally silencing a group are future capabilities.

1. **Create a reason to specialize.** Use the delayed-feedback and retention experiments to separate abilities. Keep a simple orientation probe. Measure where the ability fails and repeat across independent founder seeds.
2. **Identify a candidate function.** Inspect connectivity and activity on development runs. For example, a set of units might retain a cue during intervening choices. Choose the candidate group and prediction before testing on held-out sequences.
3. **Test causally on saved copies.** Briefly silence the candidate group. Compare with intact and sham-treated copies, and with other groups matched for size and activity. Use identical stimuli and random streams where possible. If delayed recall drops selectively while immediate orientation is preserved, then returns when the group is restored, there is evidence for a memory-related function. Replicate with new lineages.
4. **Compare a new architecture when justified.** If independent runs plateau, first check whether they routinely reach the current network limits or lack access to the required information. Compare ordinary node and edge mutations against connected-group duplication or a lightly connected, designer-supplied group. Match neuron, connection, training and compute budgets as closely as possible. Report both old-task retention and new-task performance, and distinguish designed structure from evolved specialization.

A distributed circuit can pass every behavioural test. An anatomical analogy such as a hippocampus-inspired memory circuit is a hypothesis about function. Computational experiments have shown that tasks sharing recurring subproblems can favour modular networks. This motivates testing for specialization as the Wisp curriculum grows. [Kashtan and Alon, 2005](https://pubmed.ncbi.nlm.nih.gov/16174729/)

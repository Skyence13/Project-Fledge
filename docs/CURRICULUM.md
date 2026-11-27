# Reviewing a Wisp generation and choosing its next lesson

## What the laboratory does today

The two-cue curriculum has six stages per lifetime. A candidate receives eight baseline probes, 16 teaching trials, eight recall probes, eight probes immediately after the rule reverses, 16 reteaching trials and eight reversal-recall probes. A second fresh lifetime uses the complementary assignment. One generation therefore takes 128 cohort trials. Every Wisp sees the same number of opportunities.

The lab archives the evaluated population at that boundary. With auto-advance off, it holds the generation for review. In automatic training with auto-advance on, it breeds fresh offspring and repeats the protocol with the next seeded lesson. This can continue for many generations. New curricula enter the lab as reviewed changes to the experiment. The personal Wisp has its own lifetime.

## What merits a new lesson

Morgan and the project maintainer review the evidence together. Codex can read a live report, inspect an export, run held-out comparisons, and implement the agreed next experiment. Record the curriculum revision, source revision, training budget and seeds. Keep a checkpoint on the original task so an apparent improvement on a new task can be checked for loss of an old skill.

Use development trials to pick a practical teaching budget and a proficiency criterion. Freeze both before opening the evaluation results. One **proposed first review**, subject to the pilot, would use five independently seeded populations. Select the finalist from each using training data alone. Evaluate each finalist in 32 pairs of fresh lifetimes with opposing cue assignments and 16 feedback events per teaching block. As an example decision rule, look for at least 90% accuracy on *each* of immediate recall and reversal recall in at least four of five lineages. Report the numbers separately, the first unprompted responses, variation across lineages, and the matched reflex, disabled-plasticity, uninformative-feedback and direct-association controls. Trials from the same lineage share a history. Hundreds of such responses still represent one evolutionary replicate.

The current deterministic two-choice lesson has a simple association solution that reaches 100%. When the lesson establishes teachability and stops distinguishing architectures, a harder diagnostic task becomes informative. A poor result can also motivate a new diagnostic if it isolates a suspected limitation. Generation count alone supplies neither result.

## Memory experiments available in the lab

The memory study, `wisp-delay-retention-v2`, is a bounded evaluation of an archived population. Each inherited genome starts fresh lifetimes and learns within them. The original population can stay paused. The **Evolution lab** continues to select on the version-one curriculum. Select **Memory experiments** in Godot or run `python -m wisp.memory_experiment --help` for batch evaluation of specific generations and founder seeds.

| Experiment | Teaching and probes | Measurement |
| --- | --- | --- |
| Delayed feedback | Teach both cues eight times each. Deliver each signed grade after zero, one, or three ungraded choices on other stimuli. The grade applies to the original choice. | First choice and four ungraded recall probes per cue. Inspect the opposite-target subset to see whether the Wisp can still distinguish cues that require different responses. |
| Retention after interference | Teach A, probe A, then present cue B eight times. One copy receives feedback on B and the matched copy sees B without feedback. Probe A first, then B and A again. | Compare the first A response between copies and check whether B was actually learned. The two cues can have the same or opposite targets. |
| Body adaptation, later | Change a simulated sensor gain, viewpoint or actuation delay while preserving a previously learned association. | Trials to recover motor accuracy and retention of the association. This establishes a virtual comparison before testing a physical avatar. |

The four possible assignments of left and right choices are equally represented, and either cue can play the role of A. The Wisp receives a cue and a signed grade. The association reference knows the scheduled feedback delay and can look up its own earlier choice. The fixed reflex, disabled-plasticity Wisp and Wisp receiving unrelated feedback establish comparison scores. The first response contributes to the four-probe score. Pooled counts include repeated trials on the same genomes. A change across delays measures performance with intervening stimuli and choices, rather than decay over elapsed seconds.

## Evolution under delayed feedback

The delayed-feedback campaign starts from the inherited genomes of a completed population. Changing the selection objective lets us ask whether descendants become better at learning from a late grade. Each candidate starts a fresh lifetime for each test. A Wisp makes a source choice, acts on intervening cues, then receives approval or correction for the source choice. Ungraded recall probes measure what it learned. Pairing each cue assignment with its opposite prevents an always-left or always-right policy from earning more than 50% across the pair.

Use immediate recall and recall after reversing the original rule as separate checks. A population that loses reversal learning while improving its delayed score has traded one capability for another. Even strong scores on all three measure behavior on these two cues. To identify a mechanism, reproduce the result across independently founded campaigns and intervene on candidate circuits in saved copies.

| Stage | What changes | What carries forward |
| --- | --- | --- |
| One intervening choice | Approval arrives after one ungraded action. | The original immediate and reversal recall tests. |
| Three intervening choices | The Wisp makes three ungraded choices before the grade arrives. | The original tests and the one-choice delay. |
| Five intervening choices | The Wisp makes five ungraded choices before the grade arrives. | The original tests and both preceding delays. |

Each generation sees four new, matched training sequence pairs with opposite cue assignments. Repeated teaching can produce good recall even when the learner applies every grade to its latest choice. A second test starts a fresh Wisp, presents cue A once, fills the delay with ungraded choices on cue B, grades the original choice, then asks for A again immediately. Both source cues and both target actions are tested. An association table that updates only the latest cue scores 50% on this one-shot test. An association table that tracks the announced delay can reach 100%.

| Ability | Weight in parent selection | Accuracy required for promotion |
| --- | ---: | ---: |
| First recall of the once-graded source cue | 40% | 75% |
| Recall after repeated delayed teaching on opposite targets | 30% | 90% |
| Original immediate recall | 15% | 90% |
| Original recall after reteaching a reversed rule | 15% | 90% |

At review, nominate the candidate from training scores before looking at its validation performance. Check each ability separately using 16 fresh sequence pairs for validation and 32 for independent confirmation. Earlier delays must also meet their thresholds. The fixed reflex, the Wisp with plasticity disabled and the Wisp receiving uninformative feedback provide comparison scores. Passing establishes a reason to increase difficulty in this campaign. The many responses from one candidate do not constitute independent evolutionary replicates.

The default review interval is five rounds. A single successful validation starts a streak. The training-selected nominee can differ between reviews. Two consecutive successful reviews trigger an independent confirmation of the second nominee. A confirmed stage advances to the next delay. A stage that reaches 60 rounds without confirmation stops for review, and the last confirmed stage completes the campaign. A later reviewer can use the saved genomes and results to choose the next question. For example, repeated success at a fixed delay motivates varying the delay between trials. If recall stalls, first compare the candidate with the controls and inspect which ability failed before increasing difficulty.

## Testing a longer-lived eligibility trace

The original learning rule overwrites its synaptic eligibility values on each choice. Its recurrent units carry activity between choices, but they do not receive a copy of the sampled action. This matters when a Wisp turns left, makes another choice, then receives approval for the earlier turn. Good recall after repeated teaching can coexist with chance-level recall after one late grade.

At a stalled stage, the local supervisor forks the evaluated population into two campaigns. Both start from the same archived genomes with zero trace persistence. The control arm keeps the original set of mutations. In the experimental arm, each rules-enabled offspring undergoing mutation has a 50% chance to spend its usual neuron-rule mutation on trace persistence. On the first such mutation, the decay factor is drawn uniformly from 0.20 to 0.95. Subsequent mutations perturb it within 0 to 0.95. On every choice the Wisp adds current signed eligibility to the previous trace multiplied by that factor. A grade updates the eligible synapses and clears the accumulated trace. Both arms retain the paired cue assignments, training and evaluation banks, mutation conditions and checks for old skills.

The supervisor advances each arm by up to five rounds per check. Once either arm qualifies, it saves both arms' results at the same round. The comparison ends at this checkpoint or at 60 rounds per arm. Examine the one-shot source measure, delayed block recall, immediate recall and reversal recall for both arms at that matched endpoint. For a nominee with positive trace decay, the review also scores an otherwise identical copy with the decay factor set to zero on the same sequences. This ablation measures how much the behavior depends on the trace on this test and has no weight in promotion decisions.

Each qualified arm can continue through the remaining stages under the existing validation and confirmation rules. An unqualified arm stays at the comparison checkpoint. Use the saved matched result for a direct comparison because later follow-up scores can reflect different challenges and training budgets. The local worker stops for research review if neither arm qualifies by the common limit or once all qualified arms have finished or exhausted their remaining stages. Broader conclusions require replicate campaigns with fresh seeds and independently founded lineages.

## Checking which earlier action a grade belongs to

The single-grade source task presents the source cue once and immediately probes that cue after feedback. A learner can store the last action for each cue, hold the signed grade, then repeat or reverse the stored action when it sees the probe. This gives a nonprivileged reference for interpreting first-probe accuracy.

In the repeated-cue diagnostic, an initial binary marker designates the early or late occurrence of a cue. Both occurrences elicit free choices. A grade for the designated choice precedes the probe. The informative condition follows the marker. The uninformative comparison crosses both possible credited occurrences with both hidden targets on the same pre-grade histories. The informative observations are a subset of that crossing. Report scores separately when the two actions agree and when they conflict. Count distinct conflict histories before interpreting the conflict score. The marker-aware lookup is supplied the public convention. A fresh archived Wisp has had no teaching on it.

Use `python -m wisp.marked_recall --run PATH --generation ROUND --seed SEED --split validation` for a registered evaluation. Confirmation uses the same seed and the distinct `confirmation` split. Check intact, trace-disabled, nonplastic, cue-indexed and marker-aware learners. Evaluate earlier abilities on a separately seeded bank using `wisp.mixed_delay`. Preserve the original source task and its reports.

## Practising the early/late marker

Start two copies of the same archived nominee with the same random state. Both receive 16 episodes in which the marker identifies which choice earned the grade. After each probe, the teaching arm receives an accurate grade on its response. The control receives a balanced grade assigned independently of the hidden target. Both arms then face fresh test episodes without feedback on the probe.

Score the informative marker and the fully crossed, uninformative marker separately. Report the number of distinct test histories in which the early and late actions conflict, including the count under each marker. Practice can change which histories produce conflicting choices. Report the paired change in overall recall across all matched tests alongside the difference between the two conflict scores.

Run `python -m wisp.marker_practice --run PATH --generation ROUND --seed SEED --split validation` after registering the source and study budget. Confirmation uses the same seed with the separate `confirmation` split. Evaluate the earlier lessons with a separately seeded bank of fresh copies.

## Preparing a successor study

While a bounded study is underway, the research owner may stage a proposal in the local campaign's `proposals/` directory. Record the expected predecessor, question, eligible outcome, source, budget, controls, stopping rule and conditions requiring a fresh review. A proposal has no execution effect. Keep it outside `continuations/` and leave the active path with its current owner.

At the review boundary, the research owner checks the archived reports, earlier-skill scores, existing children and operator holds under the shared writer locks. Only then may it register a single child, pin the predecessor review and source fingerprint, assign unused seeds, and advance the active path. A retry follows the registered child. Dashboard samples and health checks have no launch authority.

## When to experiment with brain structures

A founder has four recurrent-network units including two designated motor units. Wiring-enabled descendants can add or remove connections, add or delete hidden units, or duplicate a hidden unit and part of its connectivity. Current limits are 12 units and 64 connections per genome. Rule-enabled descendants can change leaky/adaptive dynamics. During a lifetime, the current eligibility rule changes synapses feeding the motor units. Those mechanisms already allow circuits to differ. Duplicating an entire connected group, assigning anatomical regions, and experimentally silencing a group are future capabilities.

1. **Create a reason to specialize.** Use the delayed-feedback and retention experiments to separate abilities. Keep a simple orientation probe. Measure where the ability fails and repeat across independent founder seeds.
2. **Identify a candidate function.** Inspect connectivity and activity on development runs. For example, a set of units might retain a cue during intervening choices. Choose the candidate group and prediction before testing on held-out sequences.
3. **Test causally on saved copies.** Briefly silence the candidate group. Compare with intact and sham-treated copies, and with other groups matched for size and activity. Use identical stimuli and random streams where possible. If delayed recall drops selectively while immediate orientation is preserved, then returns when the group is restored, there is evidence for a memory-related function. Replicate with new lineages.
4. **Compare a new architecture when justified.** If independent runs plateau, first check whether they routinely reach the current network limits or lack access to the required information. Compare ordinary node and edge mutations against connected-group duplication or a lightly connected, designer-supplied group. Match neuron, connection, training and compute budgets as closely as possible. Report both old-task retention and new-task performance, and distinguish designed structure from evolved specialization.

A distributed circuit can pass every behavioural test. An anatomical analogy such as a hippocampus-inspired memory circuit is a hypothesis about function. Computational experiments have shown that tasks sharing recurring subproblems can favour modular networks. This motivates testing for specialization as the Wisp curriculum grows. [Kashtan and Alon, 2005](https://pubmed.ncbi.nlm.nih.gov/16174729/)

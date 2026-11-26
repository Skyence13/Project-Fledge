# Project Fledge implementation handoff

A Wisp is one individual learning mind. The laboratory evolves populations of virtual Wisps and lets a person raise, teach and resume a particular individual. The long-term goal is a single neural framework that learns through ordinary interaction and carries a personal history between a Godot avatar and a physical body.

## Decision order

1. Use the [Wisp implementation brief](Project_Fledge_Wisp_Implementation_Brief.md) for the first experiment. From the **first generation**, both neuron dynamics and wiring may mutate. Include fixed-rule and fixed-wiring lineages as experimental controls. Early Wisps have two light sensors, two turn choices and feedback. The lesson is chosen after instantiation.
2. Use the [mind research proposal](Project_Fledge_Mind_Research.md) for the longer-term programme. It describes predictive body models, working and episodic memory, consolidation, curiosity and tests for natural teaching. Its original first-assignment paragraph predates the Wisp brief.
3. Use the [original learning system design](Project_Fledge_Design.md) for the two-world concept, body interfaces, earlier exemplar baseline, and later human and physical acceptance tests. Its first Codex assignment and repository-state snapshot describe the design phase.

## Research directions

- Cross inherited neuron-rule variation with inherited topology variation. Track learning during one lifetime separately from changes inherited by later generations. An individual checkpoint and a genome have different purposes.
- Measure teachability, delayed retention, correction, adaptation, and compute per completed lifetime. Vary timing in simulated time. Evaluate finalists on unseen lessons and compare with simple learners given the same teaching evidence.
- When a reusable neuron configuration emerges, fork matched lineages into continuing, gradually reduced, and frozen mutation of its inherited rule. Preserve the individual's synaptic learning. Evaluate a new task and older lessons side by side.
- Let circuits duplicate, diverge and specialize. Compare lightly scaffolded and freely evolving topologies. Temporarily silence candidate groups on saved copies and compare against matched random groups before attributing a function.
- Benchmark the owner's lab PC before choosing worker count and large population sizes. Later, move a qualified individual to a bounded physical body and test bidirectional transfer.

## Continuation

The [README](../README.md) gives the runnable entry points and the scope of the current prototype.

Use [the curriculum review plan](CURRICULUM.md) to decide what evidence warrants the next lesson or a new circuit mutation. Its proposed pilot thresholds and intervention experiments are design decisions for review. The running lab automatically breeds generations within the current protocol.

The interactive lab supports counterbalanced lifetimes, manual or automatic teaching, an adjustable clock and continuous generation advancement. The lineage archive stores completed populations and the progress of the active run. Forking from a selected generation creates an independent line of offspring. The original line remains available. Distinguish these lineage-level branch records from a full pedigree for individual Wisps.

The lab can benchmark the current workload and adjust acceleration, and supports 16, 32 or 64 candidates across the four conditions. Inspect the current run at `GET /lab/development` or in **Plan & development**. Scores describe performance on the current lesson; claims about generalization require held-out trials. Archive versions that predate the phase counters retain their original pooled fitness. The report identifies missing phase-level evidence.

The **Memory experiments** tab evaluates an archived cohort on delayed feedback and retention after learning a second cue. The lab starts one finite study with `POST /lab/next-plan/start`. The default source is the latest completed generation of the active lineage. Supply `{"branch":"main","generation":1}` to use a specific archive checkpoint. Read status, progress, source, report and saved path at `GET /lab/next-plan`. The CLI can read a portable ZIP or recreate founders. Both entry points use the same evaluation code. Each study starts disposable lifetimes, so the active lineage and personal Wisp retain their progress. The transfer bundle carries the most recent successful study.

Reproduce the small trials and automated tests first. Compare immediate and delayed accuracy, the opposite-target trials, and first recall of A after teaching B with its matched control. Record hardware, runtime, seeds, search budget and source revision for longer studies. To make delayed learning an evolutionary objective, implement separate development and held-out lesson banks and check for loss of the original reversal skill.

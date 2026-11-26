# Project Fledge

A **Wisp** is one learning individual. In this first experiment, a teacher privately chooses what two light patterns mean. The Wisp sees one pattern, looks left or right, and receives approval or correction. The same neural code runs in the Godot teaching room and in a faster population experiment. You can keep and resume one Wisp while other lineages evolve.

For a fresh Codex session, start with [the handoff](docs/HANDOFF.md) and [the Wisp implementation brief](docs/Project_Fledge_Wisp_Implementation_Brief.md).

## Open the laboratory

Run the commands from the repository root with Python 3.11 or later. The teaching room also requires a Godot 4.6 executable. The Python code uses the standard library.

```sh
python -m wisp.play
```

The launcher finds `godot` or `godot4` on your PATH. On Windows you can also give it the executable path.

```powershell
python -m wisp.play --godot "C:\Tools\Godot\Godot.exe"
```

### Run and watch a population

The **Evolution lab** opens with 16 Wisps, four candidates for each combination of fixed or evolvable neuron dynamics and wiring. The four groups share their founders. Select a Wisp in the roster to inspect its genome, network size, learning rate, responses and score. Changing the selection leaves the experiment running.

Each Wisp experiences baseline probes, teaching, recall, a rule reversal, reteaching, and reversal recall. The six stages repeat with the opposite cue assignment in a fresh lifetime. The lab displays the current stage, cue and progress. Fitness is the fraction of correct responses during the two recall stages, when feedback is withheld. These are selection scores on the current lessons. The batch experiment below provides an independent held-out evaluation.

| Control | What it does |
| --- | --- |
| Manual | **Step lesson** advances one cue for every Wisp. During teaching, **Teach left** or **Teach right** grades the choices against the direction you select. |
| Automatic | **Start** delivers the scheduled lessons and grades responses. **Pause** holds the current trial position. |
| Computer speed check | Benchmarks a disposable copy of this population on the host. One cohort trial gives every Wisp one choice. The measured rate, recommended ceiling, current target, and achieved pace have different meanings. |
| Real time / Accelerated | Real time advances one cohort trial per second. Adaptive acceleration uses calibrated capacity. The pace menu also offers fixed targets up to the measured ceiling. Both modes use the same cues, neural updates and feedback. Manual trials advance on a click. |
| Auto-advance generations | When enabled with automatic training running, the lab selects parents and breeds the next population at each boundary. When disabled it holds the completed cohort for inspection. **Next generation** breeds one generation on demand. |

For an unattended run, choose **Automatic**, **Accelerated · adaptive**, **Auto-advance generations**, then **Start**. Acceleration triggers a speed check if this population needs one. The clock accounts for measured work as it runs and repeats the check after a material change in network complexity. The server keeps advancing when you view another tab. Keep the laboratory process running and the computer awake.

**New founder population** gives you 16, 32, or 64 Wisps (4, 8, or 16 candidates per experimental condition). The previous population stays in **Lineages**. More candidates explore more variants each generation and cost more choices per trial. To compare under the same budget, try 16 Wisps for 16 generations, 32 for 8, and 64 for 4. Each evaluates 256 candidates. Repeat with multiple seeds and compare the trained populations on held-out lessons. The batch runner's `--population` counts candidates per condition.

### View the learning plan and developmental evidence

**View full learning plan** opens the six stages of the current two-cue protocol, their trial budgets, and the planned experiments. **Generation summary** opens the observations from the current population. It shows baseline responses, immediate recall, first responses to the reversed rule, and recall after reteaching. Read the counts beside the percentages. Pending probes and observations unavailable in older archives are labelled. Under **Lineages**, select a completed generation and click **Generation summary** for its historical evidence.

The report is generated locally. **Copy summary** copies a plain-text snapshot you can paste into a review. An assistant session with local access can also read the loopback service's `GET /lab/development` while the lab is running. That endpoint observes the run without issuing commands. The project repository holds the implementation; experimental saves stay on the machine that runs the laboratory.

### Inspect history or start a branch

Open **Lineages**, select a lineage, then a saved generation. You can inspect its final population and fitness while the active population continues. Give the new lineage a name and choose **Branch from here**. Branching from generation 5 opens generation 6 with fresh offspring. The new lineage inherits the selected parents and its own reproducible mutation stream. The lesson schedule remains matched for comparison.

Use the lineage selector and **Open this lineage** to return to a previous population at its saved progress. Only one lineage trains at a time. Each completed generation is archived before the next one begins. Pausing automatic training, switching lineages, or closing the room normally also saves progress within the current generation. After an abrupt process exit, the run recovers from its last saved point. Reopened lineages start paused.

The local SQLite file sits beside the personal save with the suffix `.lineages.sqlite3`. Archive size grows as generations accumulate. Reopen with the same `--save` path to use the same archive. To run a second independent laboratory, choose another save path and add `--port 0` to use an available port.

### Teach a personal Wisp

Open the **Personal Wisp** tab. This individual has its own lifetime and checkpoint.

1. Pick which cue means left and which means right. Keep the assignment to yourself.
2. Present a cue, watch the Wisp turn its gaze, and respond to its choice. Alternate the cues over several rounds.
3. Reverse your assignment and watch how many interactions the Wisp takes to change its response.
4. Use **Save learning** to preserve this individual. The default file is `runs/personal.json`. Launching again resumes it. Use a new file with `--save runs/second-wisp.json` to start a separate lifetime. The launcher uses local port 8765.

## Run a batch comparison

```sh
python -m wisp.experiment --seed 7 --population 8 --generations 3 --workers 1 --output runs/demo
python -m wisp.play --resume runs/demo/champion.json --save runs/demo/personal.json
```

Each of four conditions starts with the same founders. Every condition selects heritable starting weights and a learning rate, and every Wisp can change its own synapses during teaching. Two independent switches determine whether offspring can change their neuron dynamics and their wiring. A checkpoint resumes an individual with its learned weights, activation, pending feedback, and random state. A new offspring starts from its genome.

| File in the run directory | What it contains |
| --- | --- |
| `report.json` | Settings, seeds, matched evaluation budgets, per-generation scores, held-out teaching and reversal curves, and controls. |
| `champion.json` | A fresh individual from the selected genome, taught in a separate demonstration and ready to resume in Godot. Selection uses training scores. |
| `timing.json` | Measured wall time and selection throughput on the machine that ran the experiment. |

The held-out trials use fresh sequences and both possible cue mappings. Compare post-teaching and post-reversal accuracy with the direct association learner, the fixed reflex, and the same Wisp with either synaptic updates disabled or feedback signs shuffled independently of its answers. Repeat with additional seeds for independent evolutionary runs. Choose a different output directory for each run you want to keep.

With the seed-7 command above, each condition used 96 training lifetimes. The selected Wisps scored 93.75–98.44% averaged across the two held-out probes after teaching and reversal. The direct association learner scored 100%. The fixed reflex, disabled synaptic learning, and uninformative feedback controls each averaged 50%. One and two workers produced identical scientific results and individual checkpoints. This single run checks the experiment and leaves the relative merits of the four structures open.

## Verify and continue

```sh
python -m unittest discover -s tests -v
```

The first lesson uses symbolic light sensors, immediate feedback, and two turn actions. The batch runner uses the same sensor/action interface as Godot and steps the neural model directly. Its timings measure learning throughput for this task. Physics-based locomotion, natural-language teaching, and transfer between bodies belong to later experiments.

The next question is whether a Wisp can retain an association through an intervening task and still assign feedback to an earlier choice. The research plan also calls for recording each offspring's parents and benchmarking throughput on the intended lab PC.

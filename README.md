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

### Test memory on a saved generation

Open **Memory experiments** and run the latest completed generation. The lab takes each archived genome, gives a fresh Wisp a short lesson, records its unprompted responses and stops. You can leave evolution paused throughout. Auto-advance in the Evolution lab continues to select offspring on the original two-cue reversal lesson.

The first memory test delivers feedback for a choice after zero, one, or three intervening choices. The second teaches one cue, teaches a second, then asks for the first again. Compare that first answer with a control that saw the second cue equally often without feedback. The results show correct responses and opportunities for the Wisps and four comparison learners. The rows for opposite cue targets require different answers for the two cues.

To evaluate an earlier generation from a portable export or reproduce the founder population, use the batch command. Choose a fresh output path for each study.

```sh
python -m wisp.memory_experiment --export runs/exports/lab.zip --branch main --generation 1 --seed 41 --repeats 8 --output runs/memory-g1.json
python -m wisp.memory_experiment --founder-seed 7 --population 16 --seed 41 --repeats 8 --output runs/memory-founders.json
```

The lab saves each completed study as JSON beside the lineage database. A portable export includes the most recent successful study and restores it in the destination lab. To transfer additional studies, copy their JSON files separately.

### Evolve a population for delayed feedback

Use a **campaign** to breed descendants of a saved population on a harder lesson. The first stage delivers each grade after one intervening choice. Later stages delay the grade by three and five choices. Each stage tests repeated teaching and a single late grade for a cue the Wisp then sees again. At each review, the candidate must also recall the original rule, learn its reversal and retain the earlier delays. The original lineage can stay paused at its saved trial. The **Memory experiments** tab displays campaign progress for the current save.

Start once from a completed generation. With the lab open, use **Lineages → Export run**, then substitute that ZIP path in the example below. This example enrolls generation 2,212 of the original lineage. Choose a completed generation that exists in your archive. With the lab closed, you can replace `--export runs/exports/lab.zip` with `--archive runs/personal.json.lineages.sqlite3`.

```sh
python -m wisp.campaign init --export runs/exports/lab.zip --branch main --generation 2212 --run runs/personal.json.campaign --name "Delayed feedback" --seed 20261126
python -m wisp.campaign run --run runs/personal.json.campaign --through 10
python -m wisp.campaign status --run runs/personal.json.campaign
```

Round 1 evaluates the enrolled genomes under the new objective. Later rounds evaluate their descendants. `--through 10` means finish round 10, including rounds already saved. Repeating that command after an interruption resumes from the last completed round. To continue, choose a higher target, for example `--through 20`. Each invocation accepts up to 20 additional rounds. A batch stops at its target or when the campaign reaches a terminal review. A second writer cannot take over a running batch.

To hold the campaign, use the command below. The last committed round is safe to resume. An interrupted round can be recomputed. The hold persists between batches. Use `resume` when you want subsequent batches to proceed.

```sh
python -m wisp.campaign pause --run runs/personal.json.campaign
python -m wisp.campaign resume --run runs/personal.json.campaign
```

The default campaign reviews a candidate every five rounds. Promotion requires two consecutive successful reviews and independent confirmation. A stage that reaches 60 rounds without promotion stops for review. Completing all three stages also stops the campaign. Read the scores and counts for each ability before extending the curriculum. See the [curriculum review plan](docs/CURRICULUM.md).

### Monitor a campaign and test the next challenge

After initializing the campaign, start its local supervisor from the repository root. It runs while this process is open and the computer is awake. The default interval between checks is five minutes.

```sh
python -m wisp.campaign_watch --run runs/personal.json.campaign
```

Each check advances at most five rounds per active population. If the primary campaign exhausts a stage's budget, the supervisor enrolls its archived population in a paired experiment. One arm keeps the original learning rule. In the other, rules-enabled descendants can inherit a mutation that preserves part of the synaptic trace of earlier choices. Both arms start with the same genomes, lesson seeds and population size.

When either arm qualifies, the supervisor records both results at the same round. Each qualified arm automatically advances through the remaining three- or five-choice challenges. Any arm that missed the cutoff stays at the comparison checkpoint. If neither arm qualifies within 60 rounds, or all qualified arms finish or exhaust a later stage's budget, the worker stops for research review. See [how to interpret the paired comparison](docs/CURRICULUM.md#testing-a-longer-lived-eligibility-trace).

**Memory experiments → Evolution campaign** shows the current phase and each arm's results. Use **Pause supervision** to hold the entire experiment and **Resume supervision** to clear the hold. The same operations are available from another terminal.

```sh
python -m wisp.campaign_watch --run runs/personal.json.campaign --status
python -m wisp.campaign_watch --run runs/personal.json.campaign --pause
python -m wisp.campaign_watch --run runs/personal.json.campaign --resume
```

The global hold persists across restarts. An in-flight generation can finish. A hold on a single arm via `wisp.campaign pause` is separate. To try one bounded check while the worker is stopped, use `--once --tick-id manual-001`. An immediate retry with the same ID preserves the completed batch. To continue unattended, relaunch the foreground command. A hosted review can report on evidence accessible to its environment. The local process performs the evolutionary work and applies stage changes on this PC.

For a separate research experiment, fork a completed **campaign round** into a fresh directory. The parent round numbers in this command belong to the delayed-feedback campaign. The stage numbers 1, 2 and 3 correspond to delays of one, three and five intervening choices. To allow trace mutation on this new branch, add `--evolve-trace` when initializing the fork.

```sh
python -m wisp.campaign fork --parent runs/personal.json.campaign --generation 20 --stage 2 --name "Longer delay" --seed 20261127 --run runs/longer-delay.campaign
```

**Lineages → Export run** includes the default campaign beside the personal save, its supervision record and hold, and each initialized comparison arm. Import restores their committed progress beside the destination save. A freshly imported copy needs a local worker to continue. For a standalone backup or a custom location, hold supervision, let outstanding work finish, stop the worker and copy the entire `.campaign` directory. On the destination, check status and clear intentional holds when ready. Each copy has its own history. The automatic-training switch on the **Evolution lab** tab controls the original lineage.

### Inspect history or start a branch

Open **Lineages**, select a lineage, then a saved generation. You can inspect its final population and fitness while the active population continues. Give the new lineage a name and choose **Branch from here**. Branching from generation 5 opens generation 6 with fresh offspring. The new lineage inherits the selected parents and its own reproducible mutation stream. The lesson schedule remains matched for comparison.

Use the lineage selector and **Open this lineage** to return to a previous population at its saved progress. Only one lineage trains at a time. Each completed generation is archived before the next one begins. Pausing automatic training, switching lineages, or closing the room normally also saves progress within the current generation. After an abrupt process exit, the run recovers from its last saved point. Reopened lineages start paused.

The local SQLite file sits beside the personal save with the suffix `.lineages.sqlite3`. Archive size grows as generations accumulate. Reopen with the same `--save` path to use the same archive. To run a second independent laboratory, choose another save path and add `--port 0` to use an available port.

### Export results or move a run to another computer

While the lab is open, go to **Lineages → Export run**. The dialog gives you the path to a ZIP in the `exports` directory beside your personal save. The snapshot includes all archived lineages and generations, the precise active trial, and the personal Wisp as it stands at export time, including a pending choice. It also contains machine-readable results and a CSV you can open in a spreadsheet. You can export before the first generation finishes.

Copy the ZIP to the destination PC and install Python 3.11+ and Godot 4.6. Use the same Project Fledge source revision on both machines. During review the source is on `prototype/wisp-lab` in [Skyence13/Project-Fledge](https://github.com/Skyence13/Project-Fledge/tree/prototype/wisp-lab). From the project root on the destination, replace the ZIP path below and choose a **new** directory for the imported run.

```sh
python -m wisp.transfer import --input "path/to/export.zip" --destination "runs/imported-lab"
python -m wisp.play --save "runs/imported-lab/personal.json"
```

If Godot is outside PATH, add `--godot "path/to/Godot"` to the second command. The imported run opens paused. Use **Computer speed check** on the destination or let adaptive mode calibrate on startup. Starting both copies gives you two independent continuations of the exported point.

The `runs` directory is local and excluded from Git. The live archive records population checkpoints and aggregate scores. Use **Save learning** separately to retain the personal Wisp between ordinary sessions. Standalone `wisp.experiment` output directories contain their own reports, timings and champion files; copy those directories separately if you want them on the new machine. For the end-of-lesson rules, proposed advancement criteria and circuit experiments, see the [curriculum review plan](docs/CURRICULUM.md).

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

The standalone [graded-vision pan experiment](docs/GRADED_PAN.md) exercises a fresh
Wisp and reference predictors on synthetic stationary and moving targets through
a brief shutter closure and changes in camera motor gain.

```sh
python -m unittest discover -s tests -v
```

The first lesson uses symbolic light sensors, immediate feedback, and two turn actions. The batch runner uses the same sensor/action interface as Godot and steps the neural model directly. Its timings measure learning throughput for this task. Physics-based locomotion, natural-language teaching, and transfer between bodies belong to later experiments.

Use the memory study to measure abilities acquired under the first lesson and the evolutionary campaign to select on delayed feedback. Both use fresh lifetimes. The research plan also calls for replication with independent founders, recording individual parentage and benchmarking throughput on the intended lab PC.

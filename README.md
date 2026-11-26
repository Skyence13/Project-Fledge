# Project Fledge

A **Wisp** is one learning individual. In this first experiment, a teacher privately chooses what two light patterns mean. The Wisp sees one pattern, looks left or right, and receives approval or correction. The same neural code runs in the Godot teaching room and in a faster population experiment. You can keep and resume one Wisp while other lineages evolve.

For a fresh Codex session, start with [the handoff](docs/HANDOFF.md) and [the Wisp implementation brief](docs/Project_Fledge_Wisp_Implementation_Brief.md).

## Teach a Wisp

Run the commands from the repository root with Python 3.11 or later. The teaching room also requires a Godot 4.6 executable. The Python code uses the standard library.

```sh
python -m wisp.play
```

The launcher finds `godot` or `godot4` on your PATH. On Windows you can also give it the executable path.

```powershell
python -m wisp.play --godot "C:\Tools\Godot\Godot.exe"
```

1. Pick which cue means left and which means right. Keep the assignment to yourself.
2. Present a cue, watch the Wisp turn its gaze, and respond to its choice. Alternate the cues over several rounds.
3. Reverse your assignment and watch how many interactions the Wisp takes to change its response.
4. Use **Save learning** to preserve this individual. The default file is `runs/personal.json`. Launching again resumes it. Use a new file with `--save runs/second-wisp.json` to start a separate lifetime. The launcher uses local port 8765.

## Evolve a population

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

The next question is whether a Wisp can retain an association through an intervening task and still assign feedback to an earlier choice. The brief also calls for resumable populations, ancestry records, and throughput measurements on the target lab PC before launching long evolutionary runs.

# Predicting where a moving sensor will see a target

Run the synthetic reference benchmark with
`python -m wisp.pan_prediction --seed 702 --bank development`.
The other banks are `validation` and `confirmation`. Each bank has separate
calibration and test streams. Reserve new seeds before evaluating a Wisp.

Five graded receptors form a one-dimensional camera. An externally moving or
stationary target is visible at ticks 0 and 1. After tick 1 a signed pan command
moves the camera, the shutter closes for tick 2, and the target reappears at
tick 3. A predictor receives the first two observations, its motor command and
the dark frame. Before the reveal it predicts a bearing in degrees. Targets
stay inside the field of view. There is no object label or body gain in the
predictor input. Calibration exposes the reveal only after each practice trial.

Each simulated body gets 12 calibration transitions and 48 fresh test trajectories.
The conditions independently cross external speed and pan command. Every body
sees the same initial trajectories. Compare last-seen bearing, constant external
motion, a nominal motor model (4 degrees per unit command), and a fitted scalar
gain. The fit uses receptor readings before and after each calibration pan.
Test the nominal body and two changed bodies (2 and 6 degrees per unit).
Report absolute angular error and the count whose prediction falls within
one degree of the target, separately for stationary and moving targets.

This deterministic linear scene is an instrument check. The fitted reference
should recover the gain exactly up to floating-point error. A predicted window
that includes the target measures localization; physical reorientation and
active selection of a view require closed-loop tests.

The same command exercises a disposable Wisp founder end to end. The public
adapter projects five receptor intensities onto two overlapping left/right
receptive fields. A separate tick carries a copy of the signed, externally
scheduled camera command. On the shuttered tick the Wisp chooses which way to
look. Intermediate motor outputs are ignored. Only practice choices receive
feedback. Scores report correct first turns, the trial count and the number
of right targets. Fresh plastic and nonplastic copies start from the same seed
on each body. Each reference model's bearing prediction is also scored by side
on the same tests. A campaign comparison needs frozen seeds and an
earlier-lesson retention control before making a learning claim.
This pilot neither reads campaign data nor changes a lineage.

## Paired archived-genome runner

`python -m wisp.archived_pan --synthetic --sensory-seed 813 --earlier-seed 809`
exercises both full-size banks with a disposable genome and fixture gains 2, 4,
and 6. The archive mode requires a source directory, generation, expected genome
fingerprint and two independently registered seeds. It reads a completed round
through SQLite in read-only mode. The command only prints JSON. Campaign holds,
registration and predecessor verification are handled by the research owner.

The archived protocol uses gains 3, 4, and 5 with 16 validation and 32
confirmation starts. Trajectories occur in mirrored pairs; each body receives
12 pan practice episodes, then 48 matched first-turn tests on separate copies
of the practiced state. Fresh probe RNG draws match across arms and bodies.
The grade-only linear comparator learns from binary practice feedback. Its
gain hypotheses are 2, 4, and 6. The continuous-reveal fit is a privileged
instrument check. Compare four neural arms and report old-task performance
both on fresh copies and after pan practice. The old post-practice probe uses
the existing two mapped lessons, eight recall and eight reversal probes per
mapping. Both arms can learn normally during this old lesson.

The registered decision uses the mean of the four changed-body by motion
accuracies and pairs all test results within each lifetime for uncertainty.
Both banks must pass the primary threshold, individual cell and neural
comparison thresholds, and retention gates. A grade-only control matching
or beating the Wisp constrains any mechanistic interpretation even when all
gates pass. This command neither registers an experiment nor promotes a Wisp.

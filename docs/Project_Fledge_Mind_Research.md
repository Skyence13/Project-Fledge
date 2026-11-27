# Project Fledge mind architecture

**Research and design proposal for Morgan Bradford · 25 November 2026**

## 1  What Fledge should learn

Build Fledge around an ongoing loop of perception, prediction, action and memory. The mind receives time-stamped sensory evidence, remembers what it did, predicts what should happen next, and revises its understanding when an outcome surprises it. A teacher can influence that process through showing, taking turns, naming and correcting. The same learning system should work again tomorrow, with a new lesson neither developer anticipated.

**First scientific question.** Can a changeable body acquire a new motor calibration while retaining what it learned from a person? Give Fledge two toys which look alike from the front. Let it move its sensor to find a distinguishing feature. Name one toy, allow a correction, and teach it something else. Later, attach a body whose camera moves differently. Measure whether Fledge remembers the name, realizes its movement prediction has become unreliable, and learns how to inspect the toy again.

| Decision | Recommendation |
| --- | --- |
| What “brain-inspired” means here | Model a handful of computational mechanisms with observable consequences. Start with recurrent activity, action-conditioned prediction, fast associative memory and slower consolidation. Study individual spiking circuits where timing or local learning is the question. |
| What remains conventional | Python can host the experiments. PyTorch can train the predictor. SQLite can journal experiences, corrections and model versions. The proposed cognitive work occurs in the evolving state, representations, retrieval dynamics and learning rules. |
| What is supplied at the start | A clock, sensor and actuator channels, a few bounded movements, rudimentary orientation, and the ability to indicate uncertainty. Track which abilities and representations came pretrained, which were engineered and which Fledge acquired in its own lifetime. |
| What we would build | A common experiment interface, a persistent learning loop, and a mechanism for sharing concepts across bodies while recalibrating body-specific predictions. This is a proposed research contribution. Its scientific novelty and behavioural value need to be established. |

**Relationship to version 0.1.** Retain the two-world body contract, local execution, visible teaching exchange and blinded naming test. Keep the original exemplar learner as a comparator. The proposed first Codex assignment now targets the mind and its experiments. Godot supplies a controlled stream of experience. The real body later tests whether the same computation survives contact with real sensors. See the companion document `Project_Fledge_Design.md`.

<!-- page -->

## 2  What existing systems demonstrate

| Research system | Result and relevance to Fledge |
| --- | --- |
| Monty · Thousand Brains Project | Learns structured object models from sensory features at measured locations and orientations. In one sim-to-real example, models learned from scans of 12 real objects were tested using patches of real RGB-D images. Try Monty on active object recognition when Fledge can estimate sensor pose. General language and social learning remain beyond this demonstration. [Code](https://github.com/thousandbrainsproject/tbp.monty) · [Experiment](https://docs.thousandbrains.org/docs/using-monty-in-a-custom-application) · [Scope](https://docs.thousandbrains.org/docs/capabilities-of-the-system) |
| iSEED · 2026 | A physical iCubHead used multimodal observations to organize experiences into lively and calm contexts in an office and at a conference. Its gaze oriented to salient social stimuli. The researchers evaluated memory and expectation before closing the loop from learned experience and personal values to action. This makes iSEED a close precedent for Fledge’s experiential memory. [Physical deployment and results](https://www.frontiersin.org/journals/neurorobotics/articles/10.3389/fnbot.2026.1920808/full) |
| Grounded word learning · Vong and colleagues | A model trained offline on 61 hours of one child’s paired visual and linguistic input learned associations between words and visible referents. A 2026 follow-up reported generalization using automatically transcribed recordings from three children. Use this as evidence for aligning senses over time. Fledge also needs to resolve ambiguous references and accept a correction in a live exchange. [2024 study](https://pubmed.ncbi.nlm.nih.gov/38300999/) · [2026 study](https://pmc.ncbi.nlm.nih.gov/articles/PMC13618753/) · [Code](https://github.com/wkvong/multimodal-baby) |
| Vocal Sandbox · Grannen and colleagues | People taught physical robots through speech, object keypoints and guided motion. A pretrained language model composed high-level behaviours, and the robot exposed its intended motion so people could correct it. Two bounded work settings demonstrate how making an interpretation visible can improve teaching. Adopt that interaction principle and record how much prior knowledge the system brings. [Study](https://proceedings.mlr.press/v270/grannen25a.html) |
| DreamerV3 and LeWorldModel | DreamerV3 learns dynamics from interaction and uses imagined trajectories to improve a policy across many control tasks. The 2026 LeWorldModel preprint and code give us a smaller comparator for learning to predict future features from pixels and actions. The authors report roughly 15 million parameters trainable in hours on one GPU in their setup. Data collection, task rewards and evaluation conditions matter when applying either result to Fledge. [DreamerV3 paper and code](https://danijar.com/project/dreamerv3/) · [LeWM preprint](https://arxiv.org/abs/2603.19312) · [LeWM code](https://github.com/lucas-maes/le-wm) |

**Use these as experiments.** Reproduce a documented example before adapting a package. Log the inputs it requires, the training it inherited, what it changes online, and the task its authors actually scored. None of the studies above establishes the full combination of live teaching, lasting memory, functional drives and transfer to a different body.

<!-- page -->

## 3  The proposed mind

| Mechanism | Starting implementation and expected behaviour |
| --- | --- |
| A working state that evolves over time | Encode visual crops, audio or word tokens, joint angles, touch and drive signals with time and uncertainty. Maintain a small recurrent network on the PC. Feed it the previous state, elapsed time and the last issued movement alongside new evidence. Briefly hiding a toy tests whether recent history helps Fledge find it again. |
| A model of what its own actions do | Give a forward predictor a copy of the motor command and body configuration. Predict the next observable feature position, joint change and their uncertainty before the next observation arrives. Compare predictions with encoder readback and the actual view. A stalled actuator, moving toy and changed camera gain should leave different evidence over multiple observations. [Human sensorimotor precedent](https://mlg.eng.cam.ac.uk/zoubin/papers/WolGhaJor95.pdf) |
| Fast episodic binding | At salient events, bind the current sensory representation, attended location, heard cue, action and outcome into an addressable trace. Retrieve by similarity to a partial cue. A single teaching event can form a tentative link. Competing episodes can make Fledge uncertain enough to look again or ask. |
| Slower generalization and replay | During quiet periods, interleave recent, older and corrected experiences to train a small association or prediction network. This could extract a regularity across encounters while old examples remain available for recall. Track exemplar and encoder versions. Rebuild affected associations when corrected evidence was already used in training. A replayed memory contributes no new independent observation. [Complementary learning systems theory](https://pubmed.ncbi.nlm.nih.gov/27315762/) |
| Attention, motivation and action selection | Choose among available actions using the current goal, cost, uncertainty and recent learning progress. Curiosity can make another viewpoint worthwhile. Social interest can make inviting the teacher to look worthwhile. Frustration can shift choice after repeated blocked progress. Test these effects with identical sensory input and altered internal state. [Goal exploration in robotics](https://www.pyoudeyer.com/ActiveGoalExploration-RAS-2013.pdf) |

**Where learning occurs.** Sensory prediction can learn from every observed transition. An association can update within one teaching exchange. A later correction addresses the particular event that caused the misunderstanding. Slow training updates shared representations using many interleaved episodes. Treat each learning speed as an independently switchable mechanism.

**Where identity lives.** Checkpoint recurrent state as appropriate, learned weights, episodic traces, confidence and persistent drives together at a consistent event boundary. Upon a body switch, carry the social goal and concept representations forward. Clear stale movement intentions and refresh the body state. A physical name-to-object link is a hypothesis until new sensory evidence supports it.

<!-- page -->

## 4  How far to take the biology

**Begin with continuously valued activity.** A small recurrent network lets us study partial observability and competing predictions without committing to cellular detail. A useful first reference is a linear forward model and a content-addressable exemplar memory. Give the neural and reference versions the same observations and interventions. Whichever predicts the held-out data better earns a role in the next experiment.

**Test neural circuits where biology suggests a specific advantage.** Suppose Fledge looks at the correct toy, looks away, and only then hears “yes.” One proposed mechanism is an eligibility trace. Recently coactive cue and action units leave a decaying mark. A later evaluative signal changes the strengths of marked connections. Compare that model with assigning feedback to the last action and to a fixed time window. Ask the teacher for clarification when several events remain plausible. A delay can be modelled with firing rates or spikes. [Neuromodulated plasticity review](https://pmc.ncbi.nlm.nih.gov/articles/PMC4717313/)

| Implementation | Research role and decision rule |
| --- | --- |
| PyTorch | Use for a small recurrent state estimator and forward predictor with checkpointable parameters. Keep sensory encoders fixed during the first online binding experiment so feature drift cannot erase the meaning of an old trace. [Project](https://pytorch.org/) |
| Nengo | Reproduce its published example that learns a new input-output association using Voja and PES learning rules. The example supplies target outputs. Build a second experiment with delayed feedback and matched capacity to examine an eligibility trace. Measure latency, interference and retention. [Working example](https://www.nengo.ai/nengo/v4.0.0/examples/learning/learn-associations.html) |
| Axon and Rubicon | Axon implements systems-level spiking models and predictive error-driven learning. Rubicon’s choice simulation models how internal drives, expected outcomes and effort affect goal selection. Use it to study why the same external cue can produce a different action when a drive changes. Reproduce the bounded choice experiment before integrating its ideas. [Axon code](https://github.com/emer/axon) · [Choice simulation](https://pkg.go.dev/github.com/emer/axon/v2/sims/choose) |
| pymdp | Try discrete active inference on one tractable social decision. Infer which toy a teacher meant and choose to inspect, ask, wait or commit based on uncertainty, expected information and cost. Specify the candidate states and observations explicitly. Compare with a calibrated confidence threshold at equal observation quality. [Software paper](https://joss.theoj.org/papers/10.21105/joss.04098) · [Code](https://github.com/infer-actively/pymdp) |

**Engineering assumptions.** Start with a local workstation, a small Godot scene and bounded sensor streams. Use synthetic observations to debug timing and a rendered camera to test perception. Measure data, memory, compute time and teacher time before expanding model size. Faster simulation can generate practice; genuine teacher corrections and live physical observations have separate budgets.

<!-- page -->

## 5  Research milestones and first Codex assignment

| Experiment | What to vary and what to measure |
| --- | --- |
| 1 · Learn the consequences of looking | Give Fledge a pan-capable sensor and brief blank intervals. Independently vary motor commands and external toy motion. Predict the toy’s next visible bearing before its return. Compare the recurrent action-conditioned predictor with last-seen, linear action-conditioned, and action-shuffled variants. Score angular error and prediction-interval coverage on held-out trajectories. Change motor gain and measure trials to recalibrate. |
| 2 · Teach and remember | Freeze the build and invent two names. Record immediate recall, then teach interfering names. Measure delayed recall, generalization to new views and mistaken identification when an object is absent. Compare exemplar-only, fast-plus-slow with interleaved replay, and equal-compute training on recent episodes only. Score first responses before giving feedback. Turn off episodic retrieval at evaluation to see what the slower learner acquired. |
| 3 · Decide to seek evidence | Give two objects matching front views and different sides. Provide a sensor that can move around them. Compare active viewing with a fixed scan and a random view under an equal movement budget. Count correct identifications, extra views, time and questions asked. Then vary teacher feedback delay to test the credit-assignment rule. |
| 4 · Carry a concept into a changed body | Keep the distinguishing visual evidence and teaching history available while changing camera and motor mapping. Compare shared concept memory plus a calibrated body model against one shared mapping and against relearning from scratch. Score both vocabulary retention and motor adaptation. Repeat with real sensory input in each teaching direction before claiming physical transfer. |

**First deliverable for Codex.** Build a mind process, a deterministic recorded-stream runner, a minimal Godot body with a rendered camera, and a common experiment harness. Implement experiment 1, including a deliberately simple competitor. A single entry point should run the same mind against recorded and live simulated streams. Log observation time, available actions, issued action, predicted next observation, uncertainty, subsequent measured outcome and any learning update. Keep hidden scene state, test assignments and toy identity on the evaluator side.

**How to choose a winner.** Predeclare test scenes and random seeds before tuning. Hold out whole trajectories and layouts, include externally moved toys and split results by stationary versus moving targets. Match sensory access and experience budgets. Report paired differences across seeds, prediction error, decision latency and compute use. Promote a mechanism only for a behavioural or explanatory advantage that survives removal and retesting. A strong linear model is itself a useful finding.

**What success would mean.** Morgan teaches Fledge once, observes when it is unsure, and later sees it use that experience to choose a better observation in another body. That combination makes Fledge’s internal model experimentally accessible. The next contribution would be a reproducible demonstration of which mechanisms made the behaviour possible.

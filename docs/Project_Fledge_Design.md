# Project Fledge learning system design

**Design for review · Version 0.1 · 25 November 2026**  
**Owner** Morgan Bradford · **Implementation target** [Skyence13/Project-Fledge](https://github.com/Skyence13/Project-Fledge)

## 1  Design decision

Build a persistent, teachable companion that can inhabit a Godot avatar and a physical robot. Morgan should be able to invent an object name, demonstrate a game, correct a misunderstanding, and see Fledge use what it learned later. Invest engineering effort in attention, learning, memory, and embodiment. Everyday teaching should require only interaction.

**The first demonstration.** After the software is frozen, Morgan picks two arbitrary names for unfamiliar objects. Morgan shows and names each object. Fledge makes its interpretation visible by looking at one. Morgan can confirm, redirect its attention, or show another view. After a restart, Fledge finds the named object among distractors in a rearranged room. A second teacher can invent a different lesson on the same build.

**The longer ambition.** While playing, Fledge learns an outcome worth reproducing, invites someone to take a turn, and later pursues the same goal in another body. Fledge can remember who taught it the game while recognizing that this body has different movements and that the toy in one world may be absent from the other.

| Design choice | Proposed starting point |
| --- | --- |
| Computing | One local Python process on the owner’s PC owns identity, learning and persistent memory. The active body supplies senses and executes bounded actions. |
| Appearance and temperament | An expressive original creature. Its appearance stays open. Seed curiosity, sociability, playfulness and mild caution. Experience shapes its preferences, rituals and skills. |
| First teaching interface | A Godot tabletop, visible pointing and gaze, ordinary short phrases, and responses that let a teacher repair misunderstandings. Typed and spoken trials have separate results. |
| First engineering target | Deliver the simulated naming lesson, its controls and its evidence. Demonstrated goals, body growth and live physical transfer have separate milestones. |

**Review focus.** Sections 2 and 7 define what counts as natural teaching and what evidence earns the first milestone. Section 6 recommends packages. Section 9 is the work order to give Codex after review. Milestone thresholds and hardware choices below are proposals. The repository was public and empty when reviewed.

<!-- page -->

## 2  Teaching through ordinary interaction

**A teaching exchange.** Morgan brings two unfamiliar toys into view, indicates one and says, “This one is a mip.” Fledge turns toward a toy. If it chose the wrong one, Morgan can say “the other one” and point again. Later, “where’s the mip?” should direct Fledge’s gaze toward the same toy from a different angle. The name, object and association are selected during play.

| What the teacher does | What the learner receives and does |
| --- | --- |
| Shows, points or moves an object | The teacher points on a displayed copy of the body-camera image. Send normalized image coordinates and frame ID after correcting for viewport scaling. The visible pointer serves as a proxy for a hand. It estimates the referent among its own visual tracks and shows where it is looking. |
| Names the object | A local speech adapter or typed transcript supplies words. A small initial set of conversational forms identifies naming, asking and correction. The object-name slot accepts a new token at runtime. |
| Confirms or corrects | “Yes” and “the other one” refer to a recent, visible gaze choice. If several choices remain plausible, Fledge looks between candidates and waits for another indication. |
| Demonstrates a goal in a later release | Morgan moves a toy onto a coaster from several directions. Fledge observes the starting and ending relations, tries to reproduce the common outcome, and uses further demonstrations to resolve ambiguity. |

**What comes built in.** Provide sensory processing, a model of elapsed time, tracking of visible objects, attention to a pointing cue, basic communicative acts and body-specific motor primitives. In the first build, the designer supplies the meaning of “where is …?”, the ability to orient, and a detector for candidate objects on a tabletop. The teacher supplies each novel word and its referent. Later a learned goal can acquire a name through the same interaction history.

**Why speech needs its own test.** A recognizer can turn a made-up word into a common word. Show the heard phrase for optional correction, support repeating or spelling a name, and keep confirmed aliases with the concept. If Fledge looked at the wrong toy, let Morgan correct the referent separately. Score trials that use spelling or typed rescue separately from unassisted speech.

**Scope of the infant analogy.** The goal is a natural teaching relationship and development through experience. The first learner has a bounded vocabulary of communicative acts, a controlled visual scene and a perceptual head start. General interpretation of arbitrary demonstrations, household scenes and social intent is a research objective.

<!-- page -->

## 3  Architecture and interfaces

**Godot** runs the world, body physics, first-person camera, teacher view and expressive animation. **The PC mind** runs perception, attention, memory, learning and action selection. **A body adapter** translates its local sensors and capabilities into observations and converts supported intentions into motor commands. An independent evaluator owns the answers used for scoring.

| Boundary | Minimum version 1 contract |
| --- | --- |
| Attach and hand over | Body announces protocol version, body ID and revision, world ID, sensor capabilities, coordinate frames, action primitives and limits. The mind issues one active session and epoch. A new epoch invalidates queued motion from the previous body. |
| Observe and teach | A time-stamped observation includes source, epoch, sequence number and units. Pair compressed camera frames with metadata by frame ID. Teaching events include transcript or pointing coordinates, time, modality and confidence where available. |
| Intend and act | An intention carries a unique action ID, epoch, action name, parameters and expiry. The body acknowledges accepted, completed, unavailable, expired or failed. A repeated action ID returns its prior status without repeating motion. |
| Track and explain | A decision records candidate objects or actions, supporting episode IDs, confidence or margin, current goal, and reason for asking or abstaining. The teacher sees gaze and short responses. Detailed traces are available in a developer view. |

**Clock and transport.** Begin with a loopback WebSocket between Godot and Python. Use versioned JSON for small messages and bounded binary camera frames. Keep the latest camera frame when processing falls behind. Preserve ordered teaching, handover and action events. Timestamp on a source monotonic clock and record host receipt time. On a remote body, estimate offset and uncertainty before associating feedback across streams. Express physical quantities in SI units and document frame axes and transforms.

**Visibility.** Perception uses the body’s camera and declared sensors. Internal track IDs expire and have no semantic meaning. Godot scene-node names, object classes, mesh paths, stable instance IDs, hidden positions and the evaluator’s answers stay on the world/evaluation side. An integration test inspects messages crossing this boundary.

**Memory.** Use SQLite for episodes, concept exemplars, associations, affect state, body calibration and schema version. Keep global concept IDs separate from world-local object instances. Record teaching source, model version and body/world context. Commit the event and learned update in one transaction keyed by event ID. Restart and replay must neither lose an acknowledged update nor teach the same event twice. Default to derived local observations with bounded media buffers. Opt-in debug capture can be reviewed and cleared.

<!-- page -->

## 4  Learning and temperament

**A first implementable learner.** Start with an inspectable visual and associative baseline. It should be possible to replay a teaching moment and see exactly which evidence changed a concept.

1. **Form candidates.** Segment distinct tabletop objects from camera pixels. Track centres across nearby frames and extract normalized colour and shape features from each crop. Keep several views per named object. Track IDs are transient and do not survive a scene reset.
2. **Resolve shared attention.** Rank visible candidates by distance to the pointing cue, temporal overlap with the naming phrase and tracking quality. If the two best candidates are too close, display the ambiguity and request another indication. Tune distance and ambiguity thresholds on development scenes.
3. **Bind and repair.** A naming event creates a tentative word-to-exemplar link. Confirmation strengthens the attended candidate. Correction retracts the mistaken evidence and allows a corrected example. Attach delayed feedback to a recent gaze decision through its timestamp and decision ID. When attribution is unclear, ask before updating. Absence of feedback leaves the association open.
4. **Recall and generalize.** At query time, compare each current crop with stored exemplars for the word. Rank matches, require a minimum similarity and a margin over the runner-up, and expose an “unfamiliar” response. A new positive example adds a view. A future frozen image encoder can replace the descriptor behind the same contract.
5. **Learn actions and outcomes.** In a later milestone, record before/after object relations during guided play. Compare candidate outcomes across demonstrations from varied starts, then try a supported movement to reach the inferred relation. For a first pushing skill, fit observed travel against commanded push and body/surface context. Compare predicted and observed outcomes after each attempt.

**Intrinsic drives and functional emotions.** Keep a few explicit state variables initially. Curiosity prefers an activity where recent experience is improving predictions. Social interest makes inviting a person or waiting for their turn a possible action. Frustration accumulates when an important goal repeatedly falls short of expected progress and decays with time or success. Bound the effects, allow quiet independent play, and make social invitations subside when unanswered. A low battery is a body-local signal. Learned games and current interests travel with the mind.

**Observable consequence.** For a blocked pushing task, compare two replays with the same perceptions and goal. With frustration enabled, repeated shortfalls should increase the likelihood of inspecting the obstruction, trying another approach or requesting help. Log the score changes. The physical or virtual body renders the chosen expression through its own capabilities.

<!-- page -->

## 5  Worlds bodies and growth

Keep **world identity**, **body configuration** and **demonstrated competence** distinct. Godot can host both Fledge’s freely designed avatar and a dimensionally matched model of the first physical body. This lets us vary one factor at a time. One mind persists throughout a handover.

| Memory or capability | Behaviour across a switch |
| --- | --- |
| Names, relationships, preferences, goals | Retain the concept and its evidence. Reacquire the local object or person. Remember which experiences came from which world. |
| Maps, object positions, traction, camera pose | Retrieve the relevant local map and calibration by world, body revision and conditions. Reobserve anything that may have moved. |
| Motor skill and confidence | Express the goal in terms of an observable outcome. Check whether the new body can execute the required primitives. Begin with its calibration and qualify predicted success with local evidence. |
| New or missing hardware | Refresh the capability description, invalidate affected calibration, and run bounded checks of the changed movements. Retain unrelated knowledge. Fall back to looking, indicating or asking when an action is unavailable. |

**Physics calibration.** Use Jolt rigid bodies, contact and fixed physics steps in a small 3D room. Start with measured dimensions, masses, camera field of view and mounting height. Once hardware exists, compare straight travel and stopping distance, turn angle, camera lag and ball roll-out on two surfaces. Fit parameters on one set of trials, then report error on held-out runs. Vary plausible friction, lighting, camera delay and actuator response within measured ranges. Add fidelity where a measured discrepancy changes a decision. Godot documents engine-specific joint behaviour and limitations. [Jolt integration](https://docs.godotengine.org/en/stable/tutorials/physics/using_jolt_physics.html)

**Real body.** A stationary camera and expressive pan axis can test word transfer before locomotion. A mobile body later adds a drive and bump/proximity inputs. The onboard controller enforces travel and speed limits, a command-expiry watchdog and a local stop. On loss of the active session it stops and clears motion. Initial movement tests use an empty area; interactions with people and pets begin with stationary observation and invitations. Mechanical form, compute board and actuators remain open design choices.

**Two-way experiment.** Teach a novel name in Godot and query it on a physical counterpart among distractors. Teach a second name or correction on the physical body and query it in Godot. Follow with a mobility upgrade that changes clearance. A convincing demonstration is Fledge keeping its destination, checking fit with the new body, and choosing to tuck an attachment or take another route.

<!-- page -->

## 6  Packages and adoption decisions

Use a small CPU-capable baseline on the personal workstation. Start compatibility testing with Python 3.11 and a pinned stable Godot 4 build with built-in Jolt. Record the exact working builds and dependency lock after a rendered smoke test. Package and model compatibility on the target PC is an implementation check.

| Adopt for the first release | What it contributes |
| --- | --- |
| [Godot 4 and Jolt](https://docs.godotengine.org/en/stable/tutorials/physics/using_jolt_physics.html) | 3D room, collisions, avatar, simulated camera and teacher interface. Use the standard editor and GDScript. |
| [Godot WebSocketPeer](https://docs.godotengine.org/en/stable/tutorials/networking/websocket.html) and [Python websockets](https://websockets.readthedocs.io/en/stable/) | Bidirectional session for teaching events, sensor frames and high-level intentions. Fast motor control stays on the body. |
| [OpenCV headless](https://pypi.org/project/opencv-python-headless/) and [NumPy](https://numpy.org/) | Segment visible toys, maintain short tracks and compare appearance features. Godot supplies the display. |
| [Python sqlite3](https://docs.python.org/3/library/sqlite3.html) and [pytest](https://docs.pytest.org/en/stable/) | Transactional local memory, replay tests and evidence that survives a restart. |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [sounddevice](https://python-sounddevice.readthedocs.io/) | Local speech recognition and microphone capture for the spoken milestone. Start with a small CPU INT8 model. Measure name fidelity and response time on the owner’s PC. |

| Add when the experiment calls for it | Adoption trigger |
| --- | --- |
| [PyTorch and DINOv2](https://github.com/facebookresearch/dinov2) | Object features fail under changed appearance or viewpoint. Test a frozen small encoder on tracked crops against the original baseline. It supplies image features; Fledge learns the word associations. |
| [MediaPipe Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) | Physical pointing is needed. Infer a pointing direction from landmarks, calibrate the camera, and retain uncertainty when objects overlap. |
| [Gymnasium](https://gymnasium.farama.org/) and [Godot RL Agents](https://github.com/edbeeching/godot_rl_agents) | Repeated policy training needs standardized observation/action spaces and batched reset/step experiments. Keep the live mind’s memory outside an episode reset. |
| [Hugging Face LeRobot](https://github.com/huggingface/lerobot) | A physical body can record demonstrations and we have an imitation-learning question. Evaluate its dataset and policy tools against the chosen hardware. |

**What we build.** The reusable teaching loop, evidence-backed memory, action selection, temperament, capability negotiation, and cross-body evaluation are Fledge project code. Build in installable stages. Record source, version, licence and model-weight terms for each adopted dependency. Fetch optional weights explicitly and pin their revisions. Research precedents include [learning skills from play](https://proceedings.mlr.press/v100/lynch20a.html) and [interactive shaping with human feedback](https://robots.media.mit.edu/portfolio/training-robots-human-feedback/).

<!-- page -->

## 7  Acceptance and evidence

These are proposed engineering targets for the first controlled room. Register the protocol, thresholds, build hash, development seeds and held-out layout seeds before scoring. Leave teaching words, their assignments and the human teaching session open until the build is frozen. Preserve failures alongside successes.

| Gate | Procedure and proposed pass condition |
| --- | --- |
| A1 · Teaching and recall | Morgan assigns two fresh words to two visually distinguishable objects after freeze. Permit up to six confirmed examples per word. Restart the learner. Run 20 balanced, shuffled two-choice trials across new layouts and views without pointing or feedback. Score the first committed gaze; abstention counts as a miss. Target at least 16/20 overall and 7/10 per word. Random choice averages 10/20. |
| A2 · Specificity and repair | Run ten queries with the named object absent; target at least eight “unfamiliar” responses. In five separate ambiguous teaching exchanges, get at least four clarification requests with no premature confirmed binding. Correct a mistaken binding, restart, then verify the corrected exemplar wins in four of five fresh presentations. |
| A3 · Personal learning | Repeat A1 using different teacher-chosen words and fresh memory on the same executable. A blank or shuffled-association control should lose the learned advantage. Record its actual score. Disable learning, teacher cues, pointer and highlighting during recall. New words reach Fledge only through teaching and queries. The evaluator alone receives the correct test assignments. |
| B · Spoken teaching | Run the same A1 task live with a microphone and capture the spoken and interpreted words. Use the same target and report results separately for unassisted speech and spelling/typed rescue. Report end-to-end latency, misheard tokens, wrong referents and abstentions. Typed success earns A only. |
| C · Skill and growth | Teach a new label for a demonstrated object relation from several starts. Change the approach and use an unseen layout. Score at least 8/10 observed goal completions. Remove the needed primitive, then attach a capable body. Verify abstention, calibration, resumed action and retained names. |
| D · Live transfer | Use new teaching words and comparable visible objects. Repeat the two-choice protocol in both directions, with matched distractors and separate scores. Record prior physical exposure, examples, lighting and body revision. Live observations are required for a physical-transfer result. |

**Does simulation help?** Fork the same initial mind into real-practice-only and virtual-plus-real conditions. Give each the same physical trial budget; evaluate on the same held-out physical tasks. Compare success, physical trials to criterion, elapsed time and engineering effort. Repeat with several seeds before drawing conclusions. The virtual room also has value as a place for shared play.

<!-- page -->

## 8  Delivery sequence and diagnostics

| Milestone | Runnable deliverable | Evidence to retain |
| --- | --- | --- |
| M0 · Connect the room | Rendered 3D tabletop, first-person camera, visible avatar gaze, Python session, local memory and reproducible launcher. | Frame visibly changes when the body turns. Capture–decision–action trace, restart, stale-command and disconnect tests pass. |
| M1 · Teach by showing | Point, name, confirm, correct and ask through the teacher view. Choose new names without touching configuration. Developer view explains candidates and updates. | A1–A3, human session transcript or consented recording, seed manifest, control scores and machine-readable results. This is the first reviewable implementation. |
| M2 · Teach by voice | Local microphone, speech adapter, transcript repair and status indication. Keep the same learner below the transcript interface. | B, speech model revision and settings, hardware details, error breakdown, response-time distribution. |
| M3 · Demonstrate and play | Learn one class of spatial goals, compose a known action with a learned name, and test growth using two virtual bodies. Introduce intrinsic drives and their behavioural tests. | C, trials on unfamiliar starts, comparison with replayed motion, affect-on/off traces, capability handover and calibration results. |
| M4 · Cross into hardware | Real camera and bounded expressive actuator, hardware adapter, matched Godot body and measured physics. Add mobile motion when the stationary handover works. | D, camera/calibration measurements, stop/watchdog checks and physical-comparison report. |

**Three failures worth diagnosing early**

- **Correct transcript, wrong toy.** Inspect candidate tracks, cue timing, score margin and the gaze that the teacher actually saw. Cross-test with the rendered pointer or a corrected transcript through the same interfaces.
- **Works only in the original arrangement.** Shuffle positions and object appearance independently, remove pointer cues, reassign the invented words, and inspect pixels and event schemas for accidental identity clues. Preserve exemplar history across scene resets.
- **Works in the avatar, fails on the robot.** Test perception, memory retrieval and actuation separately. Feed a recorded sensor trace to the mind, then test live with local observations. A successful replay narrows the fault; physical performance comes from a live trial.

**Review decisions.** The proposed first teaching channel is visible screen pointing plus text, with speech as the next delivery. The first natural skill is learning a name for an individual object. Learning a category across different exemplars and learning an action from a human demonstration are subsequent, separately scored capabilities. A first physical body can prove continuity on the desk while the mobile design is still open.

<!-- page -->

## 9  Codex implementation handoff

**Work order after design approval.** Implement M0 and M1 in Skyence13/Project-Fledge. Read the current repository and its instructions first. If it is still empty, initialize a minimal project. Use the design reviewed by Morgan as the source of scope, preserve accepted edits, and record any engineering departures. Deliver a runnable system with a live teacher view and honest test evidence.

| Proposed repository area | Responsibility |
| --- | --- |
| `sim/godot/` | Project, tabletop, photometrically simple toys, teacher view, body camera, visible gaze, Jolt configuration and body adapter. |
| `src/fledge/` | Session and schema validation, perception and tracking, joint attention, grounding and recall, action selection, SQLite persistence and trace viewer support. |
| `tests/` and `eval/` | Unit tests, replay fixtures, protocol and restart integration, rendered camera smoke, blinded evaluation harness and human-session scorecard. Keep answer keys on this side. |
| `docs/` and repository root | Reviewed design, setup, teaching instructions, dependency and model records, limitations, launcher, dependency lock and automated checks. |

1. **Get a visible round trip.** Add versioned sample messages and an adapter. Show that turning the avatar changes its next camera frame. Reject unsupported versions, stale sessions, expired actions and malformed inputs with observable results.
2. **Build one generic teaching loop.** Implement section 4 steps 1–4 behind the section 3 boundary. Let the teacher select, reposition and rotate toys, point, and enter arbitrary names in the running scene. Persist learned evidence and expose the current referent and ambiguity.
3. **Make failures reproducible.** Use tests for occlusion, equal candidates, delayed correction, missing target, duplicate feedback, process restart and handover. Test nonvisual components headlessly. Use a render-capable runner or local rendered session for the actual camera path. Report the environment used.
4. **Deliver a playable review.** Document exact setup and launch commands for Windows, how to start with fresh memory, how to resume, and how to run the blinded lesson. Provide one scripted end-to-end rehearsal and a separate owner-run lesson after build freeze. Mark owner-run scores as pending until performed.

**Completion evidence.** Supply the commit identifier, working dependency lock, tested Godot and Python builds, exact test commands and results, reproducible seed/configuration manifest, and the command that opens the interactive scene. Label each result as fixture, rendered simulation, owner interaction or live hardware. Record which milestone was actually demonstrated.

**Reviewer prompts.** Can Morgan teach two words the developers never chose? Can Morgan tell what Fledge thinks is being taught and correct it naturally? Can another developer trace the learning to a particular interaction and reproduce the result after a restart? Those three questions determine whether the first build is ready for the next stage.

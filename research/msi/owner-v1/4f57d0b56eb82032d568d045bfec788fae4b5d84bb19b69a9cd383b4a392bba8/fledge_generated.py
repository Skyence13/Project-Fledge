"""Prepare a new bounded assay in a source-only staging area, then pin and test it."""
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

from fledge_coordinator import (LAB, ROOT, WORK, Hold, Retry, codex, digest, read, require, run_command, save)


BASE = WORK / "fledge-state-swap"
BASE_REV = "2609e7dbce91bd521523e25790a9130c80388d02"
FILES = ("design.json", "proposal.md", "study.py", "test_study.py")
TESTS = """
import unittest
suite = unittest.TestLoader().discover("tests", pattern="test_generated_study.py")
if suite.countTestCases() == 0:
    raise ValueError("No calibration tests discovered")
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.testsRun == len(result.skipped):
    raise ValueError("Generated calibration tests did not pass")
"""
FIXTURE = """
import importlib, json, sys
from random import Random
from wisp.core import random_genome
from wisp.experiment import identifier
study = importlib.import_module("wisp.generated_study")
if study.PROTOCOL != sys.argv[1]:
    raise ValueError("Generated protocol does not match design")
genome = random_genome(Random(78119))
champion = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
print(json.dumps([study.evaluate(champion, 71819, 81917, s)
                  for s in ("validation", "confirmation")], allow_nan=False))
"""

FAMILY_EVALUATE = """
def evaluate_family(study, champion, design, seed, earlier_seed, split):
    from wisp.core import Genome
    from wisp.experiment import identifier
    originals = {k:v for k,v in champion.items() if k in ("genome","fingerprint")}
    ancestor = champion["frozen_ancestor"]
    subjects = {"finalist": originals, "frozen_ancestor": ancestor}
    for arm, change in design["matched_interventions"].items():
        control = subjects[change["control"]]
        genome = Genome.from_dict(control["genome"])
        edited = study.intervene(genome, arm)
        edited = Genome.from_dict(edited.to_dict())
        if design["owner_action"] == "compare_learning_rule":
            if edited.edges != genome.edges or [(n.id,n.leak,n.gain) for n in edited.nodes] != [(n.id,n.leak,n.gain) for n in genome.nodes]:
                raise ValueError("Rule intervention changed wiring or non-rule parameters")
        if design["owner_action"] == "compare_architecture":
            if edited.learning_rate != genome.learning_rate or edited.trace_decay != genome.trace_decay:
                raise ValueError("Wiring intervention changed global learning rule")
            shared = {n.id:n for n in genome.nodes}
            if any(n.id in shared and n != shared[n.id] for n in edited.nodes):
                raise ValueError("Wiring intervention altered existing neuron parameters")
            old_edges = {(e.source,e.target):e.weight for e in genome.edges}
            new_edges = {(e.source,e.target):e.weight for e in edited.edges}
            if old_edges.keys() == new_edges.keys() and set(shared)=={n.id for n in edited.nodes}:
                raise ValueError("Wiring intervention left topology unchanged")
            if any(new_edges[e]!=old_edges[e] for e in old_edges.keys() & new_edges.keys()):
                raise ValueError("Wiring intervention changed a shared edge's weight")
        fingerprint = identifier(edited)
        if fingerprint == control["fingerprint"]:
            raise ValueError("Matched intervention left its control unchanged")
        subjects[arm] = {"genome":edited.to_dict(),"fingerprint":fingerprint}
    row = study.evaluate_family(subjects, seed, earlier_seed, split)
    from wisp.pan_review import fresh_skills
    row["fresh_skills"] = fresh_skills(originals, earlier_seed, split)
    row["ancestor_fresh_skills"] = fresh_skills(ancestor, earlier_seed, split)
    def earlier_rates(scores):
        m = scores["metrics"]
        return {"mixed_recall":m["mixed_recall"]["accuracy"],**{k:v["accuracy"] for k,v in m["old_skills"].items()},
                **{phase+"_"+str(lag):v["accuracy"] for phase in ("fixed_recall","fixed_one_shot") for lag,v in m[phase].items()}}
    old = earlier_rates(row["ancestor_fresh_skills"])
    row["fresh_minus_ancestor"] = {k:v-old[k] for k,v in earlier_rates(row["fresh_skills"]).items()}
    row["retained"] = bool(row["fresh_skills"]["passed"] and min(row["fresh_minus_ancestor"].values()) >= -.05-1e-12
                           and row["post_practice"]["passed"])
    row["subject_fingerprints"] = {k:m["fingerprint"] for k,m in subjects.items()}
    return row
"""

FAMILY_FIXTURE = FIXTURE.split("print(json.dumps")[0] + FAMILY_EVALUATE + """
ancestor = random_genome(Random(78120))
champion["frozen_ancestor"] = {"genome":ancestor.to_dict(),"fingerprint":identifier(ancestor)}
design = json.loads(sys.argv[2])
print(json.dumps([evaluate_family(study, champion, design, 71819, 81917, s)
                  for s in ("validation", "confirmation")], allow_nan=False))
"""

FAMILY_DESIGN = """
This is an exact frozen-family assay. Export evaluate_family(subjects, seed, earlier_seed, split).
Its two mandatory subjects and held_out arms are finalist and frozen_ancestor, each with genome and fingerprint.
Measure both, and explicitly score transfer and earlier skills. The trusted worker adds subject_fingerprints itself.
Honor the structured decision's exact action, question and hypothesis. Copy owner_action and owner_question into design.json,
and include matched_interventions, an object keyed by the additional intervention arm, with {control:"finalist" or "frozen_ancestor"}.
For compare_learning_rule or compare_architecture provide at least one scientifically relevant actual matched genotype
intervention via intervene(genome, arm) returning a Genome, plus a registered held-out contrast against its named control.
The worker constructs the actual altered genotype, retaining every non-intervened parameter and full default 12-neuron bound.
Compare learning rules with fixed wiring. Compare architecture with fixed rules, shared neurons and shared edge weights.
For other actions use {} unless a matched genotype intervention answers the question. For all subjects use identical budget,
paired random streams and the same tasks. Test the treatment and sham on planted fixtures, and show a measurement changes.
"""

DESIGN = """Create ONE discriminating successor for a numerical Wisp learning experiment, justified by this decision and both bank reports.
All preparation uses disposable fixtures. The reference package in reference/wisp is the only research code needed.
Write exactly four deliverables: study.py, test_study.py, proposal.md and design.json. Use standard Python only.
Do not access live archives, external services or checkpoints. Do not run or start live work.
study.py will be installed as wisp/generated_study.py in a clean reference checkout; use relative package imports there.
test_study.py will be installed at tests/test_generated_study.py; use unittest and import wisp.generated_study.
Include a deterministic planted positive control and a sham or negative control that discriminate the proposed explanations. Test both.
Pure evaluation must be bounded to one immutable nominee, up to 64 independent paired lifetimes per bank, no evolution, no writes, no subprocesses, no threads, no network.
Export PROTOCOL and evaluate(champion, seed, earlier_seed, split); champion supplies genome and fingerprint (see wisp/state_swap.py).
Use fresh deterministic streams per split and lifetime, held-out mirrored or balanced probes, common action draws for matched controls.
Return a JSON dict with EXACT keys: split, lifetimes, size:{neurons,connections}, held_out:{arm:{condition:{correct,total,accuracy}}},
visible_contrasts:{key:{difference,ci95:[lo,hi]}}, gates:{key:bool}, instrumentation_passed:bool,
fresh_skills and post_practice from wisp.pan_review.fresh_skills and post_practice_summary. Use independently seeded post_practice_pair
on cloned endpoints for the prior lesson. The name visible_contrasts is a compatibility field; choose descriptive contrast keys for the actual assay.
Compute each named contrast as one arm minus a matched control on its primary condition; resample paired lifetimes for 2,000 draws.
All intervals, scores, success rules and specific opposing interpretations belong in the proposal. A scientific null or earlier-skill loss is valid.
design.json must have keys: child (new hyphenated name, lowercase), protocol (new lowercase hyphenated version name), objective (plain language),
bounds and senses (plain language), arms (at least two unique snake_case names), conditions (snake_case names), lifetimes:{validation:int,confirmation:int},
probes_per_lifetime (common integer per arm and condition), contrasts:{key:{arm,control,condition,minimum}},
and seeds:{sensory:positive int,earlier_skills:positive int}. For each contrast set gate=true iff difference >= minimum AND lower CI > 0.
Register one primary contrast first. Use distinct unused seeds. Report the exact calibrated observations and the hypothesis in the proposal.
The trusted worker classifies primary contrast as replicated only when it passes on both banks; it records secondary contrasts and earlier skills independently.
No additional dependencies. Budget time for fixture execution under Windows. Do not create a launcher or campaign instructions.
"""


def readonly_command(code, command):
    native = shutil.which("codex.exe")
    if not native:
        shim = shutil.which("codex.cmd")
        matches = list(Path(shim).parent.glob("node_modules/@openai/codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe")) if shim else []
        require(len(matches) == 1, "Codex Windows sandbox executable unavailable")
        native = str(matches[0])
    return [native, "sandbox", "-P", ":read-only", "--sandbox-state-disable-network",
            "-C", str(code.resolve()), "--", *command]


def git(code, *args):
    return run_command(["git", "-c", "safe.directory=" + code.resolve().as_posix(), *args], code).strip()


def validate_design(d):
    for field in ("child", "protocol"):
        require(re.fullmatch(r"[a-z][a-z0-9-]{2,75}", d[field]), "Invalid " + field)
    for field in ("arms", "conditions"):
        names = d[field]
        require(isinstance(names, list) and 1 <= len(names) <= 12 and len(names) == len(set(names)) and
                all(re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name) for name in names), "Invalid " + field)
    require(len(d["arms"]) >= 2 and 1 <= len(d["contrasts"]) <= 12, "Missing matched controls")
    require(set(d["lifetimes"]) == {"validation", "confirmation"} and
            all(type(n) is int and 2 <= n <= 64 for n in d["lifetimes"].values()), "Invalid lifetime budget")
    require(type(d["probes_per_lifetime"]) is int and 1 <= d["probes_per_lifetime"] <= 256,
            "Invalid probe budget")
    for name, c in d["contrasts"].items():
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) and
                {c["arm"], c["control"]} <= set(d["arms"]) and c["arm"] != c["control"] and
                c["condition"] in d["conditions"] and 0 <= c["minimum"] <= 1, "Invalid matched contrast")
    require(set(d["seeds"]) == {"sensory","earlier_skills"} and len(set(d["seeds"].values())) == 2 and
            all(type(n) is int and 0 < n < 2**63 for n in d["seeds"].values()), "Invalid seeds")
    for field in ("objective","bounds","senses"):
        require(isinstance(d[field],str) and 1 <= len(d[field]) <= 640, "Invalid " + field)


def validate_result(d, row, split):
    body = dict(row)
    if "owner_action" in d:
        subjects = body.pop("subject_fingerprints", {})
        for field in ("ancestor_fresh_skills","fresh_minus_ancestor","retained"):
            body.pop(field)
        require(set(subjects)=={"finalist","frozen_ancestor",*d["matched_interventions"]} and
                all(re.fullmatch(r"[a-f0-9]{64}",v) for v in subjects.values()),"Missing measured specimen identities")
    require(set(body) == {"split","lifetimes","size","held_out","visible_contrasts","gates",
                         "instrumentation_passed","fresh_skills","post_practice"}, "Unexpected bank fields")
    require(row["instrumentation_passed"] is True and row["split"] == split and
            row["lifetimes"] == d["lifetimes"][split], "Calibration or bank budget failed")
    require(set(row["size"]) == {"neurons","connections"} and all(type(v) is int and v > 0 for v in row["size"].values()), "Invalid size")
    require(set(row["held_out"]) == set(d["arms"]), "Invalid arms")
    total = row["lifetimes"] * d["probes_per_lifetime"]
    for scores in row["held_out"].values():
        require(set(scores) == set(d["conditions"]), "Invalid conditions")
        for cell in scores.values():
            require(set(cell) == {"correct","total","accuracy"} and cell["total"] == total and
                    type(cell["correct"]) is int and 0 <= cell["correct"] <= total and
                    cell["accuracy"] == cell["correct"] / total, "Incorrect held-out measurement")
    require(set(row["visible_contrasts"]) == set(row["gates"]) == set(d["contrasts"]), "Unexpected contrasts")
    for name, c in d["contrasts"].items():
        effect = row["visible_contrasts"][name]
        require(set(effect) == {"difference","ci95"} and len(effect["ci95"]) == 2, "Invalid contrast fields")
        lower, upper = effect["ci95"]
        actual = row["held_out"][c["arm"]][c["condition"]]["accuracy"] - row["held_out"][c["control"]][c["condition"]]["accuracy"]
        require(abs(actual-effect["difference"]) < 1e-12 and -1 <= lower <= upper <= 1 and
                row["gates"][name] is (effect["difference"] >= c["minimum"] and lower > 0), "Contrast does not match scores or rule")
    fresh, post = row["fresh_skills"], row["post_practice"]
    require(fresh["split"] == split and set(fresh["gates"]) == {
                "mixed","each_fixed","pooled_fixed_minus_mixed","each_fixed_one_shot","immediate","reversal"}
            and fresh["passed"] is all(fresh["gates"].values()) and
            set(post["difference"]) == {"recall","reversal"} and set(post["rates"]) == {"intact","nonplastic"} and
            post["body_lifetime_pairs"] == row["lifetimes"] and
            post["passed"] is all(v >= -.05-1e-12 for v in post["difference"].values()), "Older-skill result inconsistent")


def prepare_generated(packet, decision, folder):
    registry = read(ROOT / "active_research.json")
    family = None
    owner = decision.get("owner_review",{})
    if registry.get("family_lineage"):
        from fledge_lineage_worker import family_at_boundary
        family = family_at_boundary(ROOT,registry)
        require(owner.get("action") in {"diagnose","compare_learning_rule","compare_architecture","change_capability"},
                "Requested action requires a population or independent founder")
    ready = folder / "prepared.json"
    if ready.exists():
        p = read(ready)
        require(p.get("family_lineage")==family and (not family or p["owner_review"]==owner),"Ready candidate no longer matches decision or family")
        run_prepared(p, folder)
        return p
    candidate = folder / "candidate.json"
    if candidate.exists():
        p = read(candidate)
        require(p.get("family_lineage")==family and (not family or p["owner_review"]==owner),"Candidate no longer matches decision or family")
    else:
        # The logged-in service and its read-only fixture process need the workspace ACL.
        directory = folder / ("candidate-" + uuid.uuid4().hex)
        directory.mkdir()
        draft = directory / "author"
        draft.mkdir()
        require(git(BASE,"rev-parse","HEAD") == BASE_REV and not git(BASE,"status","--porcelain"), "Research reference changed")
        shutil.copytree(BASE / "wisp", draft / "reference/wisp", ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
        require(registry.get("review_sha256") == packet["review_sha256"], "Parent moved during preparation")
        siblings = sorted(p.name for p in (ROOT/"continuations").iterdir() if p.is_dir())
        used = set()
        for name in siblings:
            for item in (ROOT/"continuations"/name).glob("*.json"):
                used.update(read(item).get("seeds",{}).values())
        instruction = DESIGN + "\nPreviously used seeds " + json.dumps(sorted(used)) + "\nExisting names " + json.dumps(siblings)
        if family:
            instruction = instruction.replace("one immutable nominee","one immutable finalist and its own frozen founder") + FAMILY_DESIGN
        if (folder/"fixture-failure.json").exists():
            instruction += "\nThe last disposable attempt failed. Address this failure: " + read(folder/"fixture-failure.json")["error"]
        supplied = folder/"assisted.json"
        if supplied.exists():
            receipt = read(supplied)
            require(receipt["parent"] == packet["review_sha256"] and set(receipt["pins"]) == set(FILES),
                    "Supplied draft belongs to another completion")
            for name in FILES:
                source = folder/"assisted"/name
                raw = source.read_bytes()
                require(digest(raw) == receipt["pins"][name], "Supplied draft changed")
                (draft/name).write_bytes(raw)
        else:
            codex(draft, instruction + "\nDecision and aggregate\n" + json.dumps([decision,packet]), "workspace-write")
        try:
            for name in FILES:
                source = draft/name
                require(source.is_file() and not source.is_symlink() and 0 < source.stat().st_size < 128000,
                        "Missing or invalid generated file: " + name)
            d = read(draft/"design.json")
            validate_design(d)
            if family:
                validate_family_design(d,owner)
            require(d["child"] not in siblings and not set(d["seeds"].values()) & used, "Successor name or seed reused")
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            save(folder/"fixture-failure.json", {"parent":packet["review_sha256"],"error":str(exc)[:500]})
            raise Retry("Incomplete candidate design; preparing a corrected draft") from exc
        code = directory / "code"
        # The pinned review hash and candidate ID can put pack files beyond MAX_PATH.
        git(directory,"-c","core.longpaths=true","clone","--config","core.longpaths=true",
            "--no-local","--no-checkout",str(BASE),str(code))
        git(code,"checkout","--detach",BASE_REV)
        require(not (code/"wisp/generated_study.py").exists(), "Reference module collision")
        shutil.copyfile(draft/"study.py", code/"wisp/generated_study.py")
        shutil.copyfile(draft/"test_study.py",code/"tests/test_generated_study.py")
        git(code,"add","--","wisp/generated_study.py","tests/test_generated_study.py")
        git(code,"-c","user.name=Project Fledge MSI","-c","user.email=fledge@localhost","commit","-m",d["objective"])
        p = {"kind":"generated","child":"continuations/"+d["child"],"parent":packet["review_sha256"],
             "predecessor":registry["active_path"],"parent_cursor_sha256":digest((ROOT/"active_research.json").read_bytes()),
             "parent_result":registry["result"],"siblings":siblings,"directory":directory.name,
             "revision":git(code,"rev-parse","HEAD"),"tree":git(code,"rev-parse","HEAD^{tree}"),
             "pins":{name:digest((draft/name).read_bytes()) for name in FILES}}
        if family:
            p.update(family_lineage=family,owner_review=owner)
        save(candidate,p)
    directory, code, d = checked_files(p,folder)
    # Run independent synthetic banks in addition to the author's controls.
    try:
        run_command(readonly_command(code,[sys.executable,"-B","-c",TESTS]),code,timeout=300)
        fixture = [FAMILY_FIXTURE,d["protocol"],json.dumps(d)] if family else [FIXTURE,d["protocol"]]
        result = run_command(readonly_command(code,[sys.executable,"-B","-c",*fixture]),code,timeout=300)
        rows = json.loads(result)
        require(len(rows) == 2, "Two disposable banks required")
        for split,row in zip(("validation","confirmation"),rows,strict=True):
            validate_result(d,row,split)
    except (Hold, Retry):
        raise
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        if "windows sandbox failed" in str(exc).lower() or "CreateRestrictedToken" in str(exc):
            raise Retry("Windows read-only fixture sandbox unavailable") from exc
        # Preserve the failed candidate directory and its outputs for inspection.
        save(folder/"fixture-failure.json", {"parent":p["parent"],"error":str(exc)[:500]})
        candidate.unlink()
        raise Retry("Prepared candidate failed disposable tests; retrying design") from exc
    run_prepared(p,folder)
    save(ready,p)
    return p


def checked_files(p,folder):
    require(p["kind"] == "generated" and p["parent"] == folder.name and
            re.fullmatch(r"candidate-[a-zA-Z0-9_-]+",p["directory"]), "Invalid preparation identity")
    directory = folder/p["directory"]
    draft, code = directory/"author", directory/"code"
    require(set(p["pins"]) == set(FILES) and
            all(digest((draft/name).read_bytes()) == p["pins"][name] for name in FILES), "Candidate contents changed")
    require(git(code,"rev-parse","HEAD") == p["revision"] and git(code,"rev-parse","HEAD^{tree}") == p["tree"]
            and not git(code,"status","--porcelain"), "Tested evaluator changed")
    d = read(draft/"design.json")
    validate_design(d)
    if p.get("family_lineage"):
        validate_family_design(d,p["owner_review"])
    require(p["child"] == "continuations/"+d["child"], "Successor child changed")
    return directory, code, d


def validate_family_design(d, owner):
    require(d["owner_action"]==owner["action"] and d["owner_question"]==owner["successor_question"],
            "Authored study does not match the sealed owner action or question")
    bases={"finalist","frozen_ancestor"}
    changes=d["matched_interventions"]
    require(bases <= set(d["arms"]) and isinstance(changes,dict) and not (bases & changes.keys()),
            "Missing frozen family controls")
    if owner["action"] in {"compare_learning_rule","compare_architecture"}:
        require(bool(changes),"Requested comparison requires an actual matched intervention")
    for arm,change in changes.items():
        require(set(change)=={"control"} and change["control"] in bases and arm in d["arms"] and
                any(c["arm"]==arm and c["control"]==change["control"] for c in d["contrasts"].values()),
                "Actual intervention has no matched registered control")


def run_prepared(p,folder,launch=False):
    checked_files(p,folder)
    name = "prepared.json" if launch or (folder/"prepared.json").exists() else "candidate.json"
    require(read(folder/name) == p, "Candidate pin file changed")
    args = [sys.executable,"-B",str(WORK/"fledge_generic_worker.py"),str(folder/name)]
    run_command(args + (["--run"] if launch else []),LAB)

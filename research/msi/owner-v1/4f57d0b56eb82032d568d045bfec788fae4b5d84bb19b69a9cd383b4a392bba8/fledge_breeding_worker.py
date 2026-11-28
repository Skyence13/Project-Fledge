"""Register and run the bounded stationary one-blank population child on MSI."""
from pathlib import Path
import sys
from types import SimpleNamespace

from fledge_coordinator import LAB, ROOT, STATE, WORK, checked, digest, read, require, save

REFERENCE = WORK / "fledge-one-blank-pr13"
REVISION = "c3035d31e4636c6c05991e3e37b2439013b039b2"
TREE = "4fef6dcfd3c8e40cd810a90ded34b409962e4872"
CHILD = "one-blank-stationary-breeding-r5"
PARENT = "symmetric-receiver-offset-r5"
SOURCE = "mixed-transfer-trace-r30"
OBJECTIVE = "Does selection on mirrored one-blank trials improve both left and right localization relative to the finalist's frozen ancestor while retaining earlier skills?"
SELECTION = "Training only. Rank the weaker blank side first, balanced blank accuracy second, and visible accuracy third. Select a changed final-generation descendant and its own frozen founder."
FILES = ("fledge_blank_breeding.py", "test_fledge_blank_breeding.py", "fledge_breeding_worker.py",
         "bounded_evaluation_worker.py", "fledge_breeding_proposal.md")


def prepare_manifest(folder, active):
    require(active["active_path"] == "continuations/" + PARENT and
            active["state"] == "bounded_study_unscored" and active["review_sha256"] == folder.name,
            "Unexpected breeding boundary")
    names = sorted(p.name for p in (ROOT/"continuations").iterdir() if p.is_dir())
    require(CHILD not in names, "Breeding child already exists")
    for name in names:
        registration = ROOT/"continuations"/name/"preregistration.json"
        if registration.is_file():
            require(read(registration).get("protocol") != "one-blank-stationary-breeding-v1",
                    "Existing registered breeding comparison")
    return dict(kind="stationary_breeding", child="continuations/"+CHILD, parent=folder.name,
                parent_cursor_sha256=digest((ROOT/"active_research.json").read_bytes()),
                siblings=names, pins={name:digest((WORK/name).read_bytes()) for name in FILES})


def execute(manifest):
    require(Path.cwd().resolve() == LAB, "Run the breeding worker from the pinned lab directory")
    # Import the scientific implementation from the exact reference checkout.
    sys.path.insert(0, str(REFERENCE))
    import wisp
    require(Path(wisp.__file__).resolve().parent == REFERENCE/"wisp", "Wisp import reference differs")
    import fledge_blank_breeding as science
    from bounded_evaluation_worker import TwoBankReview
    from fledge_events import emit
    from fledge_generated import git
    from source_review_worker import fp, json_bytes

    p = read(manifest)
    require(manifest.resolve() == STATE/p["parent"]/"prepared.json" and
            p["kind"] == "stationary_breeding" and p["child"] == "continuations/"+CHILD,
            "Unexpected breeding manifest")

    def check_code():
        require(set(p["pins"]) == set(FILES) and all(digest((WORK/n).read_bytes()) == h for n,h in p["pins"].items()),
                "Breeding implementation or design changed")
        require(git(REFERENCE,"rev-parse","HEAD") == REVISION and git(REFERENCE,"rev-parse","HEAD^{tree}") == TREE
                and not git(REFERENCE,"status","--porcelain"), "Breeding reference differs")

    check_code()
    predecessor = checked(ROOT/"continuations"/PARENT/"review.json",p["parent"])
    source = read(ROOT/"continuations"/PARENT/"preregistration.json")
    founders = science.load_founders(ROOT/"continuations"/SOURCE,source["parent_population_sha256"],source["parent_record_sha256"])
    identities = [{k:m[k] for k in ("group","slot","fingerprint")} for m in founders]

    class BreedingReview(TwoBankReview):
        def verify_code(self):
            check_code()

        def collect(self, plan):
            self.plan = plan
            self.cursor_at_start = read(ROOT/"active_research.json")
            if self.cursor_at_start != plan:
                from fledge_coordinator import verified
                require(self.cursor_at_start.get("active_path")==plan["active_path"] and
                        self.cursor_at_start.get("state")=="bounded_study_complete","Breeding cursor changed")
                verified(ROOT,self.cursor_at_start)
            anchor = digest((self.child/"preregistration.json").read_bytes())
            members = founders
            progress_path = self.child/"training-progress.json"
            progress = read(progress_path) if progress_path.exists() else None
            banks_started = any(self.child.glob(CHILD+"-*.json")) or (self.child/"review.json").exists()
            for i in range(science.GENERATIONS+1):
                self.guard()
                require(read(ROOT/"active_research.json") == self.cursor_at_start,"Breeding cursor changed")
                path = self.child/f"generation-{i:02d}.json"
                if path.exists():
                    record = read(path)
                    content = record["checkpoint"]
                    require(digest(json_bytes(content)) == record["sha256"] and content["previous"] == anchor
                            and content["generation"] == i, "Generation checkpoint differs")
                else:
                    require(not banks_started and not (progress and progress["generation"] >= i)
                            and not any(self.child.glob(f"generation-{i+1:02d}.json")),
                            "Missing saved generation")
                    following, summary, nominee = science.generation(members,i)
                    content = dict(previous=anchor, generation=i, next_members=following,
                                   population=summary, nominee=nominee)
                    self.write(path,dict(checkpoint=content,sha256=digest(json_bytes(content))))
                anchor = digest(path.read_bytes())
                if progress and progress["generation"] == i:
                    require(progress["checkpoint_sha256"] == anchor,"Generation progress differs")
                if not progress or progress["generation"] < i:
                    progress = dict(generation=i,generation_budget=science.GENERATIONS,
                                    checkpoint_sha256=anchor,population=content["population"]["population"])
                    self.write(progress_path,progress)
                    emit("evolving",CHILD,OBJECTIVE,i,science.GENERATIONS)
                members = content["next_members"]
            self.finalist = content["nominee"]
            self.selection_digest = anchor
            return super().collect(plan)

        def write(self,path,data):
            if hasattr(self,"cursor_at_start"):
                require(read(ROOT/"active_research.json")==self.cursor_at_start, "Breeding owner changed during work")
            super().write(path,data)

        def evaluate(self, split):
            bank = science.evaluate_bank(self.finalist,founders,split)
            bank["selection_checkpoint_sha256"] = self.selection_digest
            bank["retained"] = science.retained(bank)
            return bank

    def validate(bank, split):
        require(bank["selection_checkpoint_sha256"] == worker.selection_digest and
                bank["retained"] is science.retained(bank), "Selected finalist or retention summary differs")
        science.validate_bank(bank,split)

    def summarize(banks):
        require(set(banks) == {"validation","confirmation"}, "Two breeding banks required")
        passed = all(b["passed"] for b in banks.values())
        return dict(result="bilateral_improvement_and_retention_"+("replicated" if passed else "criteria_not_met"),
                    retained_both=all(b["retained"] for b in banks.values()), by_split=banks,
                    generations_bred=science.GENERATIONS, finalist=worker.finalist["fingerprint"],
                    frozen_ancestor=worker.finalist["founder_fingerprint"])

    spec = SimpleNamespace(CHILD=CHILD,PARENT=PARENT,SOURCE=SOURCE,GENERATION=5,
        PARENT_SHA=p["parent_cursor_sha256"],REVIEW_SHA=p["parent"],PARENT_RESULT=predecessor["result"],
        PARENT_STATE="bounded_study_unscored",SIBLINGS=set(p["siblings"]),CODE=REFERENCE,
        REVISION=REVISION,TREE=TREE,MODULE="fledge_blank_breeding", MODULE_FILE=WORK/"fledge_blank_breeding.py",
        PROTOCOL=science.PROTOCOL,PREFIX=CHILD,SEEDS=science.SEEDS,SEED_COUNT=len(science.SEEDS),
        PROPOSAL=CHILD+".md",PROPOSAL_SOURCE=WORK/"fledge_breeding_proposal.md",
        PROPOSAL_SHA=p["pins"]["fledge_breeding_proposal.md"],OBJECTIVE=OBJECTIVE,
        BOUNDS=f"One population of {len(founders)} from source round five. Breed exactly {science.GENERATIONS} generations. Stop after one training-selected finalist and two independent banks.",
        CONTROLS=["finalist","frozen_ancestor","last_visible_decoder"],
        THRESHOLDS=dict(science.THRESHOLDS,
                        senses="Two graded light inputs show a stationary target. On one-blank probes the second frame is dark.",
                        success="On each side, gain at least five points over the finalist's frozen ancestor with a positive paired lower bound on both banks. Retain visible localization and earlier skills."),
        PUBLISH_EVENTS=True,validate_bank=validate,summarize=summarize,
        EXTRA_REGISTRATION=dict(study_kind="evolution",generation_budget=science.GENERATIONS,
                                selection_rule=SELECTION,stop_after_review=True,neuron_cap=12,
                                source_fingerprints=identities,implementation_sha256=p["pins"]))
    worker = BreedingReview(spec)
    worker.main()


if __name__ == "__main__":
    execute(Path(sys.argv.pop(1)).resolve())

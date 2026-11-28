"""Prepare, pin and execute bounded studies on the registered breeding family."""
from copy import deepcopy
import json
import math
from pathlib import Path
from random import Random
import sys
from types import SimpleNamespace

from fledge_coordinator import LAB, ROOT, STATE, WORK, Retry, checked, codex, digest, inside, read, require, run_command, save
from fledge_breeding_worker import REFERENCE, REVISION, TREE

FIRST = "97181cdb3ce4eae516de3500fc6420fb1744dc4965a3cb23041b1a02cc664167"
INITIAL_CHILD = "continuations/one-blank-stationary-breeding-r5"
CURVE = "finalist-blank-response-curve"
GUIDED = "mechanism-guided-descendant"
FILES = ("fledge_lineage_worker.py","fledge_lineage_science.py","test_fledge_lineage.py","fledge_lineage_protocol.md",
         "fledge_blank_breeding.py","bounded_evaluation_worker.py","fledge_prepare.py","fledge_generic_worker.py","fledge_generated.py","fledge_architecture.py")
FEATURES=("left_target","right_target","worst_blank_side","balanced_blank","visible","neutral_displacement",
          "left_evidence","right_evidence","weakest_sensitivity")
MECHANISMS={"rightward_bias_replicated","rightward_sensitivity_replicated","both_components_replicated"}


def blocked(folder,decision,reason):
    receipt={"review_sha256":decision["review_sha256"],"owner_review":decision["owner_review"],"reason":reason}
    target=folder/"unsupported-decision.json"
    if not target.exists() or read(target)!=receipt:
        save(target,receipt)
    raise Retry(reason)


def selection_recipe(packet,decision,folder):
    owner=decision["owner_review"]
    if owner["action"]=="confirm_independent_lineage":
        active=read(ROOT/"active_research.json")
        prior=checked(inside(ROOT,active["review_path"]),packet["review_sha256"])
        if not prior.get("selection_recipe") or not prior.get("selection_question"):
            return blocked(folder,decision,"No verified matched selection recipe at this boundary and no enrolled independent pair for the requested confirmation")
        return {"ranking":prior["selection_recipe"],"question":owner["successor_question"],"replicates":packet["review_sha256"],
                "original_question":prior["selection_question"],"origin":"independently initialized procedural founder"}
    design=folder/"selection-design.json"
    if not design.exists():
        import tempfile
        with tempfile.TemporaryDirectory(dir=folder) as scratch:
            prompt=("Specify one bounded matched breeding experiment answering this owner decision. The baseline ranks worst blank side, balanced blank and visibility. "
                    "Both arms use eight members, three offspring generations, 12 neurons, matched environments and the frozen family. The candidate uses a linear "
                    "selection score over "+str(FEATURES)+". Held-out metrics are the two original blank sides, visibility, bilateral graded transfer and older skills. "
                    "If this cannot genuinely answer this question, return a JSON object with only unsupported_reason. If and only if the owner actually requests the "
                    "preregistered response to a replicated mechanism in the immediately preceding study (bias penalty, weaker directional sensitivity, or combined), "
                    "you may specify preregistered_mechanism equal to the exact result instead of coefficients. Otherwise return JSON with only coefficients "
                    "(nonzero numeric weights, absolute sum <= 4), rationale and question (exact owner successor_question). No markdown. Decision and measurements: "+json.dumps([owner,packet]))
            text=codex(Path(scratch),prompt,"read-only").strip()
            data=json.loads(text.removeprefix("```json").removesuffix("```").strip())
            if data.get("unsupported_reason"):
                return blocked(folder,decision,str(data["unsupported_reason"]))
            require(data.get("question")==owner["successor_question"] and isinstance(data.get("rationale"),str) and data["rationale"].strip(),
                    "Selection design differs from decision")
            coefficients=data.get("coefficients",{})
            conditional=data.get("preregistered_mechanism")
            require((conditional in MECHANISMS and conditional==packet["result"] and not coefficients) or
                    (not conditional and bool(coefficients) and set(coefficients)<=set(FEATURES) and
                    all(type(v) in (int,float) and math.isfinite(v) and v!=0 for v in coefficients.values()) and
                    sum(abs(v) for v in coefficients.values())<=4),"Unbounded or unsupported training selection score")
            save(design,{"parent":packet["review_sha256"],"owner":owner,"recipe":data})
    saved=read(design)
    require(saved["parent"]==packet["review_sha256"] and saved["owner"]==owner,"Frozen selection plan no longer matches decision")
    rank=saved["recipe"].get("preregistered_mechanism") or {"coefficients":saved["recipe"]["coefficients"]}
    return {"ranking":rank,"question":owner["successor_question"],
            "rationale":saved["recipe"]["rationale"],"origin":"tested training-only selection weights"}


def initial_lineage(root, active, review):
    require(active["review_sha256"]==FIRST and active["active_path"]==INITIAL_CHILD,"Unexpected finalist source")
    checkpoint = INITIAL_CHILD+"/generation-04.json"
    fingerprints = set()
    for name,h in active["reports"].items():
        bank = checked(inside(root,INITIAL_CHILD+"/"+name),h)["bank"]
        fingerprints.add((bank["finalist_fingerprint"],bank["founder_fingerprint"],bank["founder_group"],
                          bank["founder_slot"],bank["selection_checkpoint_sha256"]))
    require(len(fingerprints)==1,"Banks name different finalists")
    finalist,ancestor,group,slot,pin = fingerprints.pop()
    record = checked(inside(root,checkpoint),pin)["checkpoint"]["nominee"]
    require(record["fingerprint"]==review["finalist"]==finalist and record["founder_fingerprint"]==review["frozen_ancestor"]==ancestor
            and record["group"]==group and record["founder_slot"]==slot,"Finalist and frozen ancestor differ")
    return dict(checkpoint=checkpoint,checkpoint_sha256=pin,member_keys=["checkpoint","nominee"],finalist=finalist,
                frozen_ancestor=ancestor,group=group,founder_slot=slot,source_path=active["source_path"],
                source_round=active["source_round"],population_sha256=active["parent_population_sha256"],
                record_sha256=active["parent_record_sha256"],origin_review=FIRST)


def family_at_boundary(root,active):
    review = checked(inside(root,active["review_path"]),active["review_sha256"])
    if active["review_sha256"]==FIRST:
        return initial_lineage(root,active,review)
    lineage = review.get("family_lineage",active.get("family_lineage"))
    require(isinstance(lineage,dict) and lineage.get("origin_review")==FIRST,"Missing sealed family lineage")
    expected_ancestor=(active.get("independent_enrollment") or {}).get("fingerprint",active["family_lineage"]["frozen_ancestor"])
    require(lineage["frozen_ancestor"]==expected_ancestor and
            lineage["population_sha256"]==active["parent_population_sha256"],"Unexpected family ancestry")
    checked(inside(root,lineage["checkpoint"]),lineage["checkpoint_sha256"])
    return lineage


def prepare_family(packet,decision,folder,breeding=False):
    from fledge_source_archive import archive_source,verify_source
    from fledge_generated import git
    path = folder/"prepared.json"
    active = read(ROOT/"active_research.json")
    parent = packet["review_sha256"]
    require(active["review_sha256"]==parent==folder.name,"Family boundary moved")
    require(git(REFERENCE,"rev-parse","HEAD")==REVISION and git(REFERENCE,"rev-parse","HEAD^{tree}")==TREE and
            not git(REFERENCE,"status","--porcelain"),"Frozen reference changed")
    lineage = family_at_boundary(ROOT,active)
    science = GUIDED if breeding else CURVE
    owner=decision.get("owner_review",{})
    require(owner.get("action")==("diagnose" if not breeding else owner.get("action")) and
            (not breeding or owner.get("action") in {"breed","confirm_independent_lineage"}),"Owner decision does not authorize requested family experiment")
    recipe=selection_recipe(packet,decision,folder) if breeding else None
    mechanism=recipe["ranking"] if recipe else None
    independent=None
    if recipe and "replicates" in recipe:
        sys.path.insert(0,str(REFERENCE))
        from wisp.core import random_genome
        from wisp.experiment import identifier
        seed=int(parent[:14],16)+281130
        fingerprint=identifier(random_genome(Random(seed)))
        require(fingerprint not in {lineage["finalist"],lineage["frozen_ancestor"]},"Independent founder collides with family")
        independent={"seed":seed,"fingerprint":fingerprint,"generation":0,"method":"random_genome","reference":REVISION,
                     "replication_parent":parent,"comparison_scope":"selection from independently initialized founder"}
    pins = {name:digest((WORK/name).read_bytes()) for name in FILES}
    siblings = sorted(x.name for x in (ROOT/"continuations").iterdir() if x.is_dir())
    child = science+"-"+parent[:10]
    require(child not in siblings,"Successor already enrolled")
    if path.exists():
        manifest = read(path)
        require(manifest["parent"]==parent and manifest["pins"]==pins and manifest["family_lineage"]==lineage and
                manifest["child"]=="continuations/"+child and manifest["siblings"]==siblings and manifest["owner_review"]==owner
                and manifest["selection_recipe"]==recipe and manifest["independent_enrollment"]==independent,"Prepared family plan changed")
        verify_source(WORK,WORK/"fledge-source-history",manifest["execution"])
    else:
        run_command([sys.executable,"-B","-m","unittest","test_fledge_lineage"],WORK)
        if breeding:
            run_command([sys.executable,"-B",str(WORK/"test_fledge_lineage.py"),"--selection-fixture",json.dumps(mechanism)],WORK)
        execution = archive_source(WORK,WORK/"fledge-source-history","lineage-v1",pins,REVISION,TREE)
        manifest = {"kind":"lineage","parent":parent,"child":"continuations/"+child,"parent_path":active["active_path"],
                    "parent_cursor_sha256":digest((ROOT/"active_research.json").read_bytes()),"parent_result":active["result"],
                    "family_lineage":lineage,"siblings":siblings,"mode":science,"mechanism":mechanism,"pins":pins,
                    "execution":execution,"owner_action":owner["action"],"owner_question":owner["successor_question"],"owner_review":owner,
                    "independent_enrollment":independent,"selection_recipe":recipe}
        save(path,manifest)
    run_command([sys.executable,"-B",str(WORK/"fledge_lineage_worker.py"),str(path)],LAB)
    return manifest


def load_family(root,lineage):
    """Read immutable genome data locally. Only hashes and measurements leave the worker."""
    import fledge_blank_breeding as prior
    from wisp.core import Genome
    from wisp.experiment import identifier
    record = checked(inside(root,lineage["checkpoint"]),lineage["checkpoint_sha256"])
    member = record
    for key in lineage["member_keys"]:
        member = member[key]
    require(identifier(Genome.from_dict(member["genome"]))==member["fingerprint"]==lineage["finalist"],"Selected genome changed")
    if "independent_seed" in lineage:
        from wisp.core import random_genome
        specimen=random_genome(Random(lineage["independent_seed"]))
        require(identifier(specimen)==lineage["frozen_ancestor"],"Independent founder fingerprint changed")
        return member,{"genome":specimen.to_dict(),"fingerprint":lineage["frozen_ancestor"]}
    founders = prior.load_founders(inside(root,lineage["source_path"]),lineage["population_sha256"],lineage["record_sha256"])
    ancestor = [m for m in founders if (m["group"],m["slot"])==(lineage["group"],lineage["founder_slot"])]
    require(len(ancestor)==1 and ancestor[0]["fingerprint"]==lineage["frozen_ancestor"],"Frozen family ancestor changed")
    return member,ancestor[0]


def execute(manifest):
    require(Path.cwd().resolve()==LAB,"Run lineage worker from the registered lab")
    from fledge_source_archive import verify_source
    p=read(manifest)
    require(manifest==STATE/p["parent"]/"prepared.json" and p["kind"]=="lineage","Unknown family manifest")
    require(p["execution"]["source_sha256"]==p["pins"] and set(p["pins"])==set(FILES),"Incomplete source execution receipt")
    verify_source(WORK,WORK/"fledge-source-history",p["execution"])
    sys.path.insert(0,str(REFERENCE))
    import wisp
    require(Path(wisp.__file__).resolve().parent==REFERENCE/"wisp","Wrong simulation import")
    from bounded_evaluation_worker import TwoBankReview
    import fledge_lineage_science as sc
    from fledge_events import emit
    from fledge_generated import git
    from source_review_worker import json_bytes
    lineage=p["family_lineage"]
    base,ancestor=load_family(ROOT,lineage)
    enrollment=p["independent_enrollment"]
    if enrollment:
        from wisp.core import random_genome
        from wisp.experiment import identifier
        independent=random_genome(Random(enrollment["seed"]))
        require(identifier(independent)==enrollment["fingerprint"] and enrollment["reference"]==REVISION,
                "Independent enrollment is not reproducible")
        base=ancestor={"genome":independent.to_dict(),"fingerprint":enrollment["fingerprint"]}
    breeding=p["mode"]==GUIDED
    require(breeding or p["mode"]==CURVE,"Invalid scientific mode")
    mode=sc.BREEDING if breeding else sc.DIAGNOSTIC
    seed_base=(28112820 if p["parent_result"] in MECHANISMS else int(p["parent"][:14],16)+281140) if breeding else 28112810
    seeds={k:seed_base+i for i,k in enumerate(("training","validation","confirmation","validation_earlier","confirmation_earlier"))}
    prefix=Path(p["child"]).name
    if enrollment:
        seeds["independent_founder"]=enrollment["seed"]
    question=p["owner_question"]
    class FamilyReview(TwoBankReview):
        def verify_code(self):
            require(git(REFERENCE,"rev-parse","HEAD")==REVISION and git(REFERENCE,"rev-parse","HEAD^{tree}")==TREE and
                    not git(REFERENCE,"status","--porcelain"),"Reference commit changed")
            require(set(p["pins"])==set(FILES),"Incomplete executable pins")
            verify_source(WORK,WORK/"fledge-source-history",p["execution"])
        def write(self,path,data):
            if hasattr(self,"owned"):
                require(read(ROOT/"active_research.json")==self.owned,"Family owner changed")
            super().write(path,data)
        def collect(self,plan):
            self.owned=read(ROOT/"active_research.json")
            require(self.owned==plan or (self.owned.get("state")=="bounded_study_complete" and
                    self.owned.get("active_path")==plan["active_path"]), "Unexpected family owner")
            self.out=deepcopy(lineage)
            if enrollment:
                self.out.update(independent_seed=enrollment["seed"],frozen_ancestor=enrollment["fingerprint"],
                                group="independent",founder_slot=0,replicates=enrollment["replication_parent"])
            self.members={"finalist":base,"frozen_ancestor":ancestor}
            if breeding:
                self.breed()
            return super().collect(plan)
        def breed(self):
            start={"genome":base["genome"],"fingerprint":base["fingerprint"],
                   "base_fingerprint":base["fingerprint"],"parent_fingerprint":None}
            cohorts={arm:[start]*sc.POP for arm in ("guided","baseline")}
            anchor=digest((self.child/"preregistration.json").read_bytes())
            progress=self.child/"training-progress.json"
            old=read(progress) if progress.exists() else {}
            for i in range(sc.GENS+1):
                self.guard()
                require(read(ROOT/"active_research.json")==self.owned,"Family owner changed")
                path=self.child/f"generation-{i:02d}.json"
                if path.exists():
                    rec=read(path)
                    content=rec["checkpoint"]
                    require(digest(json_bytes(content))==rec["sha256"] and content["previous"]==anchor and
                            content["generation"]==i,"Damaged generation checkpoint")
                else:
                    require(old.get("generation",-1)<i and not list(self.child.glob(prefix+"-*.json")) and
                            not (self.child/"review.json").exists() and not (self.child/f"generation-{i+1:02d}.json").exists(),
                            "Missing sealed generation")
                    arms={}
                    for arm in cohorts:
                        self.guard()
                        branch=p["mechanism"] if arm=="guided" else "baseline"
                        arms[arm]=sc.generation(cohorts[arm],i,branch,seeds["training"],guard=self.guard)
                    content={"previous":anchor,"generation":i,"arms":arms}
                    self.write(path,{"checkpoint":content,"sha256":digest(json_bytes(content))})
                anchor=digest(path.read_bytes())
                if old.get("generation")==i:
                    require(old["checkpoint_sha256"]==anchor,"Progress and checkpoint disagree")
                if old.get("generation",-1)<i:
                    stats=content["arms"]["guided"]["population"]
                    self.write(progress,{"generation":i,"generation_budget":sc.GENS,"checkpoint_sha256":anchor,
                                         "population":stats,"arms":{k:v["population"] for k,v in content["arms"].items()}})
                    emit("evolving",prefix,question,i,sc.GENS)
                cohorts={k:v["next_members"] for k,v in content["arms"].items()}
            guided=content["arms"]["guided"]["selected"]
            self.members.update(finalist=guided,parent_finalist=base,matched_selection=content["arms"]["baseline"]["selected"])
            self.out.update(checkpoint=(self.child/path.name).relative_to(ROOT).as_posix() if self.child.is_absolute()
                            else p["child"]+"/"+path.name,checkpoint_sha256=anchor,
                            member_keys=["checkpoint","arms","guided","selected"],finalist=guided["fingerprint"])
        def evaluate(self,split):
            self.guard()
            bank=sc.evaluate(self.members,split,seeds,mode,guard=self.guard)
            bank["selection_checkpoint_sha256"]=self.out["checkpoint_sha256"]
            return bank
    def validate(row,split):
        require(row["split"]==split and row["lifetimes"]==sc.LIFETIMES[split] and row["instrumentation_passed"] and
                row["passed"]==all(row["gates"].values()) and row["fingerprints"]=={k:m["fingerprint"] for k,m in worker.members.items()}
                and row["selection_checkpoint_sha256"]==worker.out["checkpoint_sha256"],"Bank provenance or gates differ")
    def summarize(rows):
        require(set(rows)==set(sc.LIFETIMES),"Incomplete response comparison")
        result=(("guided_selection_replicated" if all(r["passed"] for r in rows.values()) else "guided_selection_criteria_not_met")
                if breeding else sc.mechanism(rows))
        return dict(result=result,retained_both=all(r["retained"] for r in rows.values()),by_split=rows,
                    family_lineage=worker.out,finalist=worker.out["finalist"],frozen_ancestor=worker.out["frozen_ancestor"],
                    generations_bred=sc.GENS if breeding else 0, scientific_classification=result,
                    selection_recipe=p["mechanism"],selection_question=question if breeding else None)
    protocol_path=WORK/"fledge_lineage_protocol.md"
    spec=SimpleNamespace(CHILD=prefix,PARENT=Path(p["parent_path"]).name,SOURCE=Path(lineage["source_path"]).name,
        GENERATION=lineage["source_round"],PARENT_SHA=p["parent_cursor_sha256"],REVIEW_SHA=p["parent"],PARENT_RESULT=p["parent_result"],
        SIBLINGS=set(p["siblings"]),CODE=REFERENCE,REVISION=REVISION,TREE=TREE,MODULE="fledge_lineage_science",
        MODULE_FILE=WORK/"fledge_lineage_science.py",PROTOCOL=mode,PREFIX=prefix,SEEDS=seeds,SEED_COUNT=len(seeds),
        PROPOSAL=prefix+".md",PROPOSAL_SOURCE=protocol_path,PROPOSAL_SHA=p["pins"][protocol_path.name],OBJECTIVE=question,
        BOUNDS="32/64 matched independent lifetimes per bank. "+("Two eight-member populations, three offspring generations." if breeding else "Two sealed genomes; no breeding."),
        CONTROLS=["finalist","frozen_ancestor","last_visible_decoder"]+(["parent_finalist","matched_selection"] if breeding else []),
        THRESHOLDS=dict(min_component=.03,positive_lower95=True,blank_bilateral_gain=.05,transfer_bilateral_gain=.03,
                        senses="Constant total light, five signed contrasts, then a common dark frame.",
                        success="Replicate neutral bias and/or the centered response to rightward evidence; verify transfer and earlier skills."),
        PUBLISH_EVENTS=True,validate_bank=validate,summarize=summarize,
        EXTRA_REGISTRATION=dict(study_kind="evolution" if breeding else "diagnostic",family_lineage=lineage,
            source_execution=p["execution"],owner_action=p["owner_action"],owner_question=p["owner_question"],neuron_cap=12,
            owner_review=p["owner_review"],independent_enrollment=enrollment,selection_recipe=p["selection_recipe"],
            generation_budget=sc.GENS if breeding else 0,selection_rule="Matched bilateral baseline versus "+str(p["mechanism"]),
            outcome_actions=p["owner_review"].get("outcome_actions") or {"rightward_bias_replicated":"Compare training selection penalizing neutral bias",
                             "rightward_sensitivity_replicated":"Compare training selection rewarding weaker-side sensitivity",
                             "both_components_replicated":"Compare selection combining both components",
                             "response_components_unresolved":"Change intervention or capability at review; no repeated curve"}))
    worker=FamilyReview(spec)
    worker.main()


if __name__=="__main__":
    execute(Path(sys.argv.pop(1)).resolve())

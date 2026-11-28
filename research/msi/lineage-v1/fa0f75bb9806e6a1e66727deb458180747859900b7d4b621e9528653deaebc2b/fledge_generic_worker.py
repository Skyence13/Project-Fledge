"""Adapt a tested declarative registration to the existing serial worker."""
from pathlib import Path
import json
import sys
from types import SimpleNamespace

from bounded_evaluation_worker import TwoBankReview
from fledge_coordinator import ROOT, STATE, read, require
from fledge_generated import FAMILY_EVALUATE, checked_files, readonly_command, validate_result


FAMILY_BANK = """
import hashlib, importlib, json, sys
from contextlib import closing
from pathlib import Path
from wisp import campaign
from wisp.core import Genome
from wisp.experiment import identifier
root, encoded, protocol, design, seed, earlier_seed, split = sys.argv[1:]
root = Path(root)
lineage = json.loads(encoded)
def checked_member():
    path = (root / lineage["checkpoint"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Checkpoint escaped archive")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != lineage["checkpoint_sha256"]:
        raise ValueError("Selected checkpoint changed")
    row = json.loads(raw)
    for key in lineage["member_keys"]:
        row = row[key]
    source = (root / lineage["source_path"]).resolve()
    if not source.is_relative_to(root):
        raise ValueError("Founder archive escaped campaign")
    if "independent_seed" in lineage:
        from random import Random
        from wisp.core import random_genome
        founder = random_genome(Random(lineage["independent_seed"])).to_dict()
    else:
        with closing(campaign._readonly(source/"campaign.sqlite3")) as db:
            pop, report = db.execute("SELECT population,report FROM rounds WHERE generation=?", (lineage["source_round"],)).fetchone()
        population = campaign._unpack(pop)
        if campaign._digest(population)!=lineage["population_sha256"] or campaign._digest(json.loads(report))!=lineage["record_sha256"]:
            raise ValueError("Founder archive changed")
        founder = population[lineage["group"]][lineage["founder_slot"]]
    if identifier(Genome.from_dict(row["genome"])) != lineage["finalist"] or identifier(Genome.from_dict(founder)) != lineage["frozen_ancestor"]:
        raise ValueError("Family genome fingerprints differ")
    return {"genome":row["genome"], "fingerprint":lineage["finalist"],
            "frozen_ancestor":{"genome":founder, "fingerprint":lineage["frozen_ancestor"]}}
study = importlib.import_module("wisp.generated_study")
if study.PROTOCOL != protocol:
    raise ValueError("Study protocol changed")
champion = checked_member()
""" + FAMILY_EVALUATE + """
print(json.dumps(evaluate_family(study, champion, json.loads(design), int(seed), int(earlier_seed), split), allow_nan=False))
"""


class GeneratedReview(TwoBankReview):
    def bank_command(self, split):
        if self.spec.FAMILY:
            s = self.spec
            args = [sys.executable, "-B", "-c", FAMILY_BANK, str(ROOT.resolve()), json.dumps(s.FAMILY),
                    s.PROTOCOL, json.dumps(s.DESIGN), str(s.SEEDS["sensory"]), str(s.SEEDS["earlier_skills"]), split]
            return readonly_command(self.code, args)
        return readonly_command(self.code, super().bank_command(split))


def make_spec(manifest):
    folder = manifest.parent
    require(folder.parent.resolve() == STATE.resolve() and manifest.name in {"candidate.json","prepared.json"},
            "Unexpected prepared manifest location")
    p = read(manifest)
    directory, code, design = checked_files(p, folder)
    primary = next(iter(design["contrasts"]))

    def summarize(rows):
        require(set(rows) == {"validation", "confirmation"}, "Two completed banks required")
        replicated = {name: all(row["gates"][name] for row in rows.values()) for name in design["contrasts"]}
        retained = all(row["fresh_skills"]["passed"] and row["post_practice"]["passed"] for row in rows.values())
        result = {"result": primary + ("_replicated" if replicated[primary] else "_criteria_not_met"),
                "replicated":replicated, "retained_both":retained, "by_split":rows}
        if p.get("family_lineage"):
            result["retained_both"] = all(row["retained"] for row in rows.values())
            result.update(family_lineage=p["family_lineage"], finalist=p["family_lineage"]["finalist"],
                          frozen_ancestor=p["family_lineage"]["frozen_ancestor"])
        return result

    return SimpleNamespace(
        CODE=code, REVISION=p["revision"], TREE=p["tree"], MODULE="wisp.generated_study",
        FAMILY=p.get("family_lineage"), DESIGN=design,
        EXTRA_REGISTRATION=({"family_lineage":p["family_lineage"],"owner_review":p["owner_review"],
                             "study_kind":"diagnostic","neuron_cap":12} if p.get("family_lineage") else {}),
        PROTOCOL=design["protocol"], SOURCE="mixed-transfer-trace-r30", GENERATION=5,
        CHILD=design["child"], PARENT=Path(p["predecessor"]).name, SIBLINGS=set(p["siblings"]),
        PARENT_SHA=p["parent_cursor_sha256"], REVIEW_SHA=p["parent"], PARENT_RESULT=p["parent_result"],
        PROPOSAL=design["child"]+".md", PROPOSAL_SOURCE=directory/"author/proposal.md", PROPOSAL_SHA=p["pins"]["proposal.md"],
        PREFIX=design["child"], PUBLISH_EVENTS=True, BANK_TIMEOUT=300, SEEDS=design["seeds"],
        OBJECTIVE=design["objective"], BOUNDS=design["bounds"], CONTROLS=design["arms"],
        THRESHOLDS={"senses":design["senses"],"contrasts":design["contrasts"],"lifetimes":design["lifetimes"],
                    "probes_per_lifetime":design["probes_per_lifetime"],"max_post_practice_drop":.05},
        validate_bank=lambda row, split: validate_result(design,row,split), summarize=summarize,
    )


if __name__ == "__main__":
    manifest = Path(sys.argv.pop(1)).resolve()
    if "--run" in sys.argv:
        require(manifest.name == "prepared.json", "A tested successor is required")
    GeneratedReview(make_spec(manifest)).main()

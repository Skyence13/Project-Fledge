"""Prepare one tested successor for a verified completion and dispatch its guarded worker."""
from pathlib import Path
import sys

from fledge_coordinator import (FIRST_CHILD, LAB, ROOT, START, STATE, WORK, digest, read,
                                require, run_command, save)


FIRST_PINS = {
    "state_swap_worker.py": "002a5ec6934b3893c8b7cf3e536b92e20d1a68ec5f974dcfbdddeafa1cdbb5b8",
    "test_state_swap_worker.py": "21dc24a264005114d9a6578f90c25f09fe4667f80deb3542b52422e5e8a09e79",
    "state_swap_proposal.md": "0185ed6f8ce782617b2f0e63f439ebc7fcf28699f4dba61c686935b57d7a90b7",
}


def first_worker(launch=False):
    for name, sha in FIRST_PINS.items():
        require(digest((WORK / name).read_bytes()) == sha, "Prepared state swap file changed: " + name)
    args = [sys.executable, "-B", str(WORK / "state_swap_worker.py")]
    return run_command(args + (["--run"] if launch else []), LAB)


def prepare(packet, decision, folder):
    parent = packet["review_sha256"]
    path = folder / "prepared.json"
    require(parent == folder.name == decision["review_sha256"], "Preparation boundary mismatch")
    from fledge_lineage_worker import FIRST as FIRST_BREEDING, prepare_family
    if parent == FIRST_BREEDING:
        return prepare_family(packet,decision,folder)
    if packet.get("family_lineage") or read(ROOT/"active_research.json").get("family_lineage"):
        action = decision.get("owner_review",{}).get("action")
        if action in {"breed","confirm_independent_lineage"}:
            return prepare_family(packet,decision,folder,breeding=True)
        from fledge_generated import prepare_generated
        return prepare_generated(packet,decision,folder)
    if packet.get("child") == "continuations/symmetric-receiver-offset-r5" and packet.get("result") == "unscored_insufficient_matching_support":
        from fledge_breeding_worker import prepare_manifest
        manifest = prepare_manifest(folder,read(ROOT/"active_research.json"))
        if path.exists():
            require(read(path)==manifest, "Breeding worker pin or boundary changed")
        else:
            run_command([sys.executable,"-B","-m","unittest","test_fledge_blank_breeding"],WORK)
            save(path,manifest)
        run_command([sys.executable,"-B",str(WORK/"fledge_breeding_worker.py"),str(path)],LAB)
        return manifest
    if parent != START:
        from fledge_generated import prepare_generated
        return prepare_generated(packet, decision, folder)
    prepared = {"parent": parent, "kind": "state_swap", "child": FIRST_CHILD, "pins": FIRST_PINS}
    if path.exists():
        require(read(path) == prepared, "Stored first successor differs")
    else:
        first_worker()
        script = ("import sys, unittest; sys.path.insert(0,sys.argv[1]); "
                  "unittest.main(module=None,argv=['test','test_state_swap_worker'],exit=True)")
        run_command([sys.executable, "-B", "-c", script, str(WORK)], LAB)
        first_worker()
        save(path, prepared)
    return prepared


def dispatch(j):
    active = read(ROOT / "active_research.json")
    require((active.get("review_sha256") == j["parent"] and active["state"] in {"bounded_study_complete","bounded_study_unscored"})
            or (active.get("prior_diagnostic_review_sha256") == j["parent"] and active["active_path"] == j["child"]
                and active["state"] == "preregistered"), "Worker no longer owns this boundary")
    stored = read(STATE / j["parent"] / "prepared.json")
    require(all(j[k] == v for k, v in stored.items()), "Journal and tested successor disagree")
    if stored.get("owner_review"):
        directory=STATE/j["parent"]
        owner=directory/"owner-decision.json"
        if not owner.exists():
            owner=directory/"decision.json"
        sealed=read(owner)
        require(sealed["review_sha256"]==j["parent"] and sealed["owner_review"]==stored["owner_review"],
                "Tested study differs from owner decision")
    if stored.get("kind") == "lineage":
        run_command([sys.executable,"-B",str(WORK/"fledge_lineage_worker.py"),str(STATE/j["parent"]/"prepared.json"),"--run"],LAB)
    elif stored.get("kind") == "stationary_breeding":
        require(stored["child"] == "continuations/one-blank-stationary-breeding-r5", "Unexpected breeding child")
        run_command([sys.executable,"-B",str(WORK/"fledge_breeding_worker.py"),str(STATE/j["parent"]/"prepared.json"),"--run"],LAB)
    elif j["parent"] == START:
        require(j["kind"] == "state_swap" and j["child"] == FIRST_CHILD and j["pins"] == FIRST_PINS,
                "Unexpected first successor")
        first_worker(launch=True)
    else:
        from fledge_generated import run_prepared
        run_prepared(stored, STATE / j["parent"], launch=True)

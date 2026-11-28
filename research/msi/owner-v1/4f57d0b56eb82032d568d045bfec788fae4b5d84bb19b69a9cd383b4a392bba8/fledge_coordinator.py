"""Completion-driven local research service. State is separate from the scientific archive."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from fledge_completion_watch import aggregate as legacy_aggregate, try_lock

WORK = Path(__file__).resolve().parent
LAB = WORK / "fledge"
ROOT = LAB / "runs/personal.json.campaign"
STATE = WORK / "fledge-coordinator-state"
START = "d517d9fd9e1f30d7eaa6c1cb0018a988369293949f45d0a5214e4169deaefc68"
FIRST_CHILD = "continuations/one-blank-state-swap-r5"
service_account = None

class Integrity(RuntimeError):
    """Stop all dispatch until the underlying inconsistency is resolved."""

class Hold(RuntimeError):
    pass

class Retry(RuntimeError):
    pass

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    return json.loads(path.read_bytes())

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        out.write((json.dumps(obj, indent=2, allow_nan=False) + "\n").encode())
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, path)

def require(condition, message):
    if not condition:
        raise Integrity(message)

def inside(base, name):
    result = (base / name).resolve()
    require(result.is_relative_to(base.resolve()) and result != base.resolve(), "Archive path escaped its directory")
    return result

def checked(path, expected):
    raw = path.read_bytes()
    require(digest(raw) == expected, f"Hash mismatch for {path.name}")
    return json.loads(raw)

def allowed(active, root):
    if (root / "supervisor/paused").exists():
        raise Hold("Supervisor paused")
    for folder in (root, *(root / "comparison").glob("*"), *(root / "continuations").glob("*")):
        if (folder / "paused").exists():
            raise Hold(f"Operator pause at {folder.name}")
        dbpath = folder / "campaign.sqlite3"
        if dbpath.is_file():
            with closing(sqlite3.connect(dbpath.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                row, = db.execute("select status from tip where id=1").fetchone()
                if json.loads(row)["state"] == "paused":
                    raise Hold(f"Operator pause at {folder.name}")
    if read(root / "supervisor/state.json")["phase"] != "research_review":
        raise Hold("Supervisor owns campaign")

def verified(root, a):
    child = inside(root / "continuations", Path(a["active_path"]).name)
    require(child == inside(root, a["active_path"]), "Unexpected child path")
    require(a["state"] in {"bounded_study_complete","bounded_study_unscored"} and a["review_path"] == a["active_path"] + "/review.json", "Unexpected completion")
    doc = checked(child / "review.json", a["review_sha256"])
    require(all(doc[k] == a[k] for k in ("result","source_fingerprint","protocol","reports","retained_both","completed_at")), "Review and registry disagree")
    previous = checked(inside(root, a["previous_review"]), a["previous_review_sha256"])
    require(previous["active_path"] == a["parent_path"] and previous["review_sha256"] == a["prior_diagnostic_review_sha256"], "Predecessor differs")
    checked(inside(root, previous["review_path"]), previous["review_sha256"])
    require(digest(inside(root, a["proposal_path"]).read_bytes()) == a["proposal_sha256"], "Proposal changed")
    plan = read(child / "preregistration.json")
    require(all(a.get(k) == v for k,v in plan.items() if k not in {"state","next_action"}), "Registration changed")
    if a["state"] == "bounded_study_unscored":
        from fledge_offset_resolution import verify_unscored
        verify_unscored(child, a, doc)
        return dict(child=a["active_path"], review_sha256=a["review_sha256"], objective=a["objective"],
                    thresholds=a["thresholds"], result=a["result"], retained_both=None, banks={},
                    unscored_reason=a["unscored_reason"])
    banks = {}
    for name, sha in a["reports"].items():
        require(Path(name).name == name, "Invalid bank filename")
        report = checked(inside(child, name), sha)
        bank = report["bank"]
        split = bank["split"]
        require(split in ("validation","confirmation") and split not in banks, "Unexpected bank identity")
        provenance = report["provenance"]
        require(provenance["split"] == split and all(provenance[k] == a[k] for k in ("protocol","source_fingerprint","source_path","source_round","research_revision","seeds")), "Bank provenance mismatch")
        require(bank["instrumentation_passed"] is True, f"{split} instrumentation failed")
        if "passed" in bank:
            require(bank["passed"] == all(bank["gates"].values()), "Bank decision inconsistent")
        summary = doc["by_split"][split]
        require(summary.keys() <= bank.keys() and all(summary[k] == bank[k] for k in summary), "Bank and review disagree")
        # Only aggregate results enter the Codex interpretation packet.
        fields = ("lifetimes","practice","approvals","held_out","references","blank_accurate_minus_control",
                  "visible_contrasts","gates","post_practice","passed","right_choice_curve","mechanism_contrasts",
                  "transfer","prior_finalist","architecture","independent_lineages","pivot","next_action",
                  "matched_contrasts","size","retained","ancestor_fresh_skills","fresh_minus_ancestor",
                  "selection_checkpoint_sha256")
        banks[split] = {k:bank[k] for k in fields if k in bank}
        older = bank["fresh_skills"]
        banks[split]["fresh_skills"] = {k:older[k] for k in ("passed","metrics","gates")}
        if a.get("study_kind") == "evolution":
            banks[split]["ancestor_fresh_skills"] = bank["ancestor_fresh_skills"]
            banks[split]["fresh_minus_ancestor"] = bank["fresh_minus_ancestor"]
            banks[split]["retained"] = bank["retained"]
    require(set(banks) == set(doc["by_split"]) == {"validation","confirmation"}, "Incomplete banks")
    if a.get("study_kind") == "evolution" or a.get("family_lineage"):
        retained = all(b["retained"] for b in banks.values())
    else:
        retained = all(b["fresh_skills"]["passed"] and b["post_practice"]["passed"] for b in banks.values())
    require(a["retained_both"] is retained, "Retention summary disagrees with banks")
    packet = dict(child=a["active_path"], review_sha256=a["review_sha256"], objective=a["objective"],
                  thresholds=a["thresholds"], result=a["result"], retained_both=a["retained_both"], banks=banks)
    packet.update({k:a[k] for k in ("study_kind","family_lineage","research_revision","controls","bounds") if k in a})
    if "family_lineage" in doc:
        packet["completed_family_lineage"] = doc["family_lineage"]
    if "source_execution" in a:
        from fledge_source_archive import verify_receipt
        verify_receipt(WORK/"fledge-source-history",a["source_execution"])
        packet["execution_commit"] = a["source_execution"]["execution_commit"]
    if a.get("study_kind") == "evolution":
        require(doc["generations_bred"] == a["generation_budget"], "Breeding budget mismatch")
        packet.update(generations_bred=doc["generations_bred"], selection=a["selection_rule"],
                      finalist=doc["finalist"], frozen_ancestor=doc["frozen_ancestor"])
        packet["training_architecture"]=training_architecture(child,a["generation_budget"],banks)
    return packet


def training_architecture(child,budget,banks):
    """Follow the worker's checkpoint chain before exposing measured training summaries."""
    anchor=digest((child/"preregistration.json").read_bytes())
    generations=[]
    for i in range(budget+1):
        path=child/f"generation-{i:02d}.json"
        raw=path.read_bytes()
        checkpoint=json.loads(raw)["checkpoint"]
        require(checkpoint["previous"]==anchor and checkpoint["generation"]==i,"Training generation ancestry differs")
        anchor=digest(raw)
        arms={}
        for name,arm in checkpoint.get("arms",{}).items():
            arch=arm["population"]["architecture"]
            arms[name]={k:arch[k] for k in ("count","neuron_cap","at_neuron_cap","mean_sensor_to_motor_nodes","breeding_to_next")}
            arms[name]["top_by_training_rank"]=[{k:r[k] for k in ("neurons","connections","sensor_to_motor_nodes")}
                                                 for r in arch["top_by_training_rank"]]
        if arms:
            generations.append(dict(generation=i,arms=arms))
    require(all(b["selection_checkpoint_sha256"]==anchor for b in banks.values()),"Banks use a different final training checkpoint")
    return generations

def run_command(args, cwd, prompt=None, timeout=1200):
    deadline = time.monotonic() + timeout
    # A CLI shim may leave a grandchild holding an inherited pipe on interruption.
    # Temporary report files let the supervisor return promptly after a failed command.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=errors, cwd=cwd,
                             encoding="utf-8", errors="replace", creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        wait_command(p, prompt, deadline)
        output.seek(0)
        errors.seek(0)
        out = output.read().decode("utf-8","replace")
        err = errors.read().decode("utf-8","replace")
    if p.returncode:
        reason = (err or out or "Command failed").strip().splitlines()[-1][:400]
        if "already owns this campaign batch" in reason or "hold" in reason.lower() or "paused" in reason.lower():
            raise Hold(reason)
        raise RuntimeError(reason)
    return out

def wait_command(p, prompt, deadline):
    observe_start=("--run" in p.args and (STATE/"standing-authority.json").is_file() and
                   service_account is not None)
    try:
        while True:
            try:
                p.communicate(prompt, timeout=2)
                break
            except subprocess.TimeoutExpired:
                prompt = None
                if time.monotonic() >= deadline:
                    raise Retry("Command timed out; saved records will be reconciled")
                if ROOT.is_dir():
                    allowed(None, ROOT)
                if observe_start:
                    active=read(ROOT/"active_research.json")
                    journal=read(STATE/"owner.json")
                    if (active.get("active_path")==journal["child"] and
                            active.get("prior_diagnostic_review_sha256")==journal["parent"] and
                            (STATE/journal["parent"]/"notice.json").exists()):
                        from fledge_research_owner import record_handoff
                        record_handoff(ROOT,STATE,active)
                        observe_start=False
                binding = STATE / "service-account.json"
                if service_account and binding.exists() and read(binding)["account"].lower() != service_account:
                    raise Hold("Handing service lock to installed account")
    except BaseException:
        if p.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill.exe","/PID",str(p.pid),"/T","/F"],capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            p.kill()
        p.communicate()
        raise

def codex(directory, prompt, mode):
    cli = shutil.which("codex.exe")
    if not cli and shutil.which("codex.cmd"):
        npm = Path(shutil.which("codex.cmd")).parent
        native = list(npm.glob("node_modules/@openai/codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"))
        cli = str(native[0]) if len(native) == 1 else shutil.which("codex.cmd")
    cli = cli or shutil.which("codex")
    if not cli:
        raise Retry("Codex CLI unavailable")
    answer = directory / "codex-answer.txt"
    answer.unlink(missing_ok=True)
    try:
        run_command([cli,"exec","--sandbox",mode,"--ephemeral","--skip-git-repo-check","-o",str(answer),"-"],directory,prompt,
                    timeout=120 if mode == "read-only" else 1200)
    except Hold:
        raise
    except RuntimeError as exc:
        raise Retry("Codex invocation incomplete: " + str(exc)) from exc
    if not answer.is_file() or not answer.read_text(encoding="utf-8").strip():
        raise Retry("Codex returned no decision")
    return answer.read_text(encoding="utf-8").strip()

def review(packet, directory):
    if packet["review_sha256"] == START:
        path = WORK / "fledge-review-receipts" / (START + ".json")
        if path.exists():
            receipt = read(path)
            require(receipt["sha256"] == START and receipt["aggregate"] == legacy_aggregate(checked(ROOT / packet["child"] / "review.json", START)), "Watcher receipt disagrees")
            return (receipt["summary"] + "\nThe worker defines retained_both as fresh and post-practice older skills passing on both banks. "
                    "Next decision: isolate learned weights versus carried activation and adaptation at the truthful endpoint using paired checkpoint swaps.")
    save(directory/"review-request.json",packet)
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        return codex(Path(temp), "Interpret this completed simulated Wisp experiment. Return a decision record with the actual validation and confirmation scores, "
            "matched-control differences, uncertainty, earlier-skill results and which criteria passed or failed. Propose one feasible mechanistic successor, "
            "stating what opposing results would mean. Stop only if an integrity problem prevents interpretation. Use only the supplied aggregate.\n"+json.dumps(packet), "read-only")

class Coordinator:
    def __init__(self, root, state, assess, prepare, dispatch, emit, guard=allowed, verify=verified):
        self.root, self.state = root, state
        self.assess, self.prepare, self.dispatch = assess, prepare, dispatch
        self.emit, self.guard, self.verify = emit, guard, verify
        state.mkdir(parents=True, exist_ok=True)

    def event(self, phase, a, progress=None):
        study, objective = a["active_path"].split("/")[-1], a["objective"]
        if phase in {"blocked","preparing_successor"} and a.get("review_sha256"):
            folder = self.state / a["review_sha256"]
            candidate = folder/"candidate.json"
            if candidate.exists():
                try:
                    draft = read(candidate)
                    design = inside(folder,draft["directory"])/"author/design.json"
                    plan = checked(design,draft["pins"]["design.json"])
                    study,objective = plan["child"],plan["objective"]
                except (KeyError,OSError,Integrity,ValueError):
                    # Use the last registered study for a blocked-status notification.
                    if phase != "blocked":
                        raise
        info = [phase,study,objective,progress,2 if progress is not None else None]
        path = self.state / "last-event.json"
        previous = read(path) if path.exists() else None
        if (phase in {"reviewing_results","preparing_successor"} and previous and
                previous[0] == "blocked" and previous[1:3] == info[1:3]):
            return
        if previous != info:
            self.emit(*info)
            save(path,info)

    def tick(self):
        journal = self.state / "owner.json"
        a = read(self.root / "active_research.json")
        def check():
            self.guard(a,self.root)
            require(read(self.root / "active_research.json") == a, "Campaign changed during decision")
        try:
            check()
            if not journal.exists():
                require(a.get("review_sha256") == START, "Initial completion differs from cutover anchor")
                save(journal,{"parent":START,"child":None})
            j = read(journal)
            if j["child"] and a["active_path"] == j["child"]:
                require(a["prior_diagnostic_review_sha256"] == j["parent"], "Successor ancestry mismatch")
                if (self.state/"standing-authority.json").exists() and (self.state/j["parent"]/"notice.json").exists():
                    from fledge_research_owner import record_handoff
                    record_handoff(self.root,self.state,a)
                if a["state"] == "preregistered":
                    return self.launch(j, a, check)
                require(a["state"] in {"bounded_study_complete","bounded_study_unscored"},"Unknown child state")
                self.verify(self.root,a)
                j = {"parent":a["review_sha256"],"child":None}
                save(journal,j)
            require(j["parent"] == a.get("review_sha256"),"Foreign research owner or changed completion")
            packet = self.verify(self.root,a)
            from fledge_research_owner import authorized, interpret, publish_decision, check_decision
            continuous = authorized(self.root,self.state,a)
            folder = self.state / j["parent"]
            folder.mkdir(exist_ok=True)
            if continuous:
                from fledge_source_archive import on_study_completion
                source=on_study_completion(self.state.parent,self.state,a)
                require(source is not None,"Completion has no recoverable registered source receipt")
                packet["execution_commit"]=source["execution_commit"]
                packet["source_archive_commit"]=source["archive_commit"]
                packet["source_provenance"]=source["provenance"]
            decision = folder / "decision.json"
            if not decision.exists():
                self.event("reviewing_results",a,len(packet.get("banks", {})))
                if continuous:
                    record = interpret(self.root,self.state,a,packet,folder)
                else:
                    result = self.assess(packet,folder)
                    record = {"review_sha256":j["parent"],"decision":result,"recorded_at":now()}
                check()
                if not decision.exists():
                    save(decision,record)
            d = read(decision)
            require(d["review_sha256"] == j["parent"] and d["decision"].strip(),"Invalid decision receipt")
            if continuous:
                if "owner_review" not in d:
                    supplemental = folder/"owner-decision.json"
                    if not supplemental.exists():
                        enriched = interpret(self.root,self.state,a,packet,folder,existing=d)
                        check()
                        save(supplemental,enriched)
                    d = read(supplemental)
                require(d["review_sha256"]==j["parent"],"Standing decision does not match completion")
                check_decision(d["owner_review"])
                publish_decision(self.state,a,d)
                self.event("review_complete",a,len(packet.get("banks",{})))
                from fledge_research_owner import preserve_milestone
                preserve_milestone(self.state,d)
                check()
            if not continuous and a.get("study_kind") == "evolution" and a.get("stop_after_review") is True:
                self.event("review_complete",a,2)
                return "Bounded breeding study reviewed; awaiting next research direction"
            if not j["child"]:
                self.event("preparing_successor",a)
                prepared = self.prepare(packet,d,folder)
                check()
                require(re.fullmatch(r"continuations/[a-z0-9][a-z0-9-]{1,90}",prepared["child"]) and not inside(self.root,prepared["child"]).exists(), "Successor must be new")
                j.update(prepared)
                require(j["parent"] == a["review_sha256"],"Prepared for different review")
                save(journal,j)
            return self.launch(j,a,check)
        except Hold:
            self.event("paused",a)
            raise

    def launch(self,j,a,check):
        check()
        permit = self.state / "cutover.json"
        if not permit.exists():
            self.event("blocked",a)
            return "awaiting verified hourly pause and idle"
        grant = read(permit)
        require(grant.get("anchor") == START and grant.get("host") == "MSI" and grant.get("hourly_paused_and_idle") is True, "Invalid cutover record")
        self.dispatch(j)
        return "worker returned; reconciling completion"

def main():
    global service_account
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--service",action="store_true",help="Wait for the installed MSI account and the serial service lock")
    parser.add_argument("--confirm-hourly-paused-and-idle",action="store_true")
    args = parser.parse_args()
    require(socket.gethostname().upper() == "MSI","Wrong service host")
    from fledge_events import emit, ensure_monitor
    from fledge_prepare import prepare, dispatch
    service = Coordinator(ROOT,STATE,review,prepare,dispatch,emit)
    if args.confirm_hourly_paused_and_idle:
        a = read(ROOT/"active_research.json")
        allowed(a,ROOT)
        verified(ROOT,a)
        require(a["review_sha256"] == START,"Cutover boundary moved; inspect latest completion")
        save(STATE/"cutover.json",dict(anchor=START,host="MSI",hourly_paused_and_idle=True,recorded_at=now(),operator=os.environ.get("USERNAME")))
        return
    binding = STATE/"service-account.json"
    account = subprocess.check_output(["whoami.exe"],text=True,creationflags=subprocess.CREATE_NO_WINDOW).strip().lower()
    service_account = account
    if args.service:
        while not binding.exists():
            time.sleep(1)
    def bound_elsewhere():
        return binding.exists() and read(binding)["account"].lower() != account
    if bound_elsewhere():
        return
    with (STATE/"service.lock").open("a+b") as lock:
        while not try_lock(lock):
            if not args.service or bound_elsewhere():
                print("Coordinator already running")
                return
            time.sleep(2)
        save(STATE/"service-session.json",dict(account=account,scheduled=args.service,pid=os.getpid(),started_at=now(),standing_workflow=1))
        next_monitor_check = 0
        while True:
            if bound_elsewhere() or (STATE/"stopped.json").exists():
                return
            try:
                if time.monotonic() >= next_monitor_check:
                    ensure_monitor()
                    if (STATE/"standing-authority.json").is_file():
                        from fledge_source_archive import on_completion
                        on_completion(WORK,STATE)
                    next_monitor_check = time.monotonic() + 30
                save(STATE/"status.json",dict(status="checking completion",pid=os.getpid(),observed_at=now()))
                status = service.tick()
                delay = 1 if status.startswith("worker returned") else 5
            except (Hold,Retry,PermissionError,sqlite3.OperationalError) as exc:
                status,delay = str(exc),12
                if not isinstance(exc,Hold):
                    active=read(ROOT/"active_research.json")
                    if (STATE/"standing-authority.json").exists():
                        from fledge_research_owner import record_blocker
                        record_blocker(STATE,active,status)
                    service.event("blocked",active)
            except Exception as exc:
                status = "Integrity or worker stop: "+str(exc)
                save(STATE/"stopped.json",dict(error=status,at=now()))
                save(STATE/"status.json",dict(status=status,pid=os.getpid(),observed_at=now()))
                active=read(ROOT/"active_research.json")
                service.event("blocked",active)
                if (STATE/"standing-authority.json").exists():
                    from fledge_research_owner import record_blocker
                    record_blocker(STATE,active,status,integrity=True)
                print(status)
                return
            save(STATE/"status.json",dict(status=status,pid=os.getpid(),observed_at=now()))
            if args.once:
                print(status)
                return
            time.sleep(delay)

if __name__ == "__main__":
    sys.modules["fledge_coordinator"] = sys.modules[__name__]
    if (STATE/"stopped.json").exists():
        sys.exit("Coordinator stopped. Resolve the recorded error before restoring service.")
    main()

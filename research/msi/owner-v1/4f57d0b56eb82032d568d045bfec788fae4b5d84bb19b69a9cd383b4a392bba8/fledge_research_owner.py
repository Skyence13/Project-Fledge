"""Standing authority, completion context, and idempotent research notices for MSI."""
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile

from fledge_coordinator import Integrity, Retry, checked, codex, digest, inside, now, read, require, save


ANCHOR = "97181cdb3ce4eae516de3500fc6420fb1744dc4965a3cb23041b1a02cc664167"
ACTIONS = {"breed","diagnose","compare_learning_rule","compare_architecture","change_capability","confirm_independent_lineage"}
POLICY = "fledge-monitor/RESEARCH_OWNER.md"
FORMAT = """
Return a single JSON object. All output is for the research owner. The notice is also shown on the private monitor.
notice: a short standalone aggregate update for Morgan and Rook (maximum 500 characters), giving the most important result and action.
decision: a concise scientific decision covering both held-out banks, changes and regressions by condition, retention against the actual founder, simplest matched baseline, recent studies, what was inherited and what was learned within lifetime.
lineage_count: integer count of independent lineages supporting the claim. Use zero for insufficient evidence.
capability: short capability name reused across related studies. limiter: what the evidence discriminates and what remains uncertain.
history_decision: explain why the last two related studies do or do not justify another test of the same question. Name them when available.
architecture: evidence for capacity, connectivity or rule constraints; explicitly say when unmeasured. Do not adopt a change based on one lineage.
action: one of breed, diagnose, compare_learning_rule, compare_architecture, change_capability, confirm_independent_lineage.
successor_question: one feasible, discriminating question consistent with the selected action (maximum 500 characters).
outcome_actions: array of objects with nonempty outcome and action strings. Include at least two genuinely different actions.
milestone: boolean for a meaningful gain, regression, change of direction or preservation need.
Preserve any sealed decision already supplied for this completion. Do not invent missing comparisons, support, transfer or architecture measurements.
"""


def authorized(root, state, active):
    grant = state/"standing-authority.json"
    if not grant.exists():
        return False
    g = read(grant)
    require(g == {"version":1,"host":"MSI","owner":"msi\\morga","anchor":ANCHOR,
                  "scope":"one_bounded_study_at_a_time"}, "Invalid standing research authority")
    record = active
    seen = set()
    while record.get("review_sha256") != ANCHOR:
        key = record.get("previous_review_sha256")
        require(key and key not in seen,"Standing authority ancestry does not reach the approved review")
        seen.add(key)
        parent = checked(inside(root,record["previous_review"]),key)
        require(parent.get("review_sha256") == record["prior_diagnostic_review_sha256"] and
                parent.get("active_path")==record["parent_path"],"Standing authority predecessor mismatch")
        record = parent
    checked(inside(root,record["review_path"]),ANCHOR)
    return True


def history(root,state,active):
    """Follow archive links, keeping aggregate evidence and no genome or trial data."""
    records = []
    seen = set()
    item = active
    while item.get("previous_review_sha256"):
        previous = checked(inside(root,item["previous_review"]),item["previous_review_sha256"])
        key = previous.get("review_sha256")
        if not key:  # legacy campaign record predating diagnostic review receipts
            break
        require(key not in seen and key==item.get("prior_diagnostic_review_sha256"),"Unexpected history link")
        seen.add(key)
        doc = checked(inside(root,previous["review_path"]),key)
        banks = {s:{k:bank[k] for k in ("visible_contrasts","matched_contrasts","mechanism_contrasts",
                                         "gates","fresh_minus_ancestor","independent_lineages") if k in bank}
                 for s,bank in doc.get("by_split",{}).items()}
        decision = state/key/"decision.json"
        supplement=decision.with_name("owner-decision.json")
        prior = read(supplement) if supplement.exists() else read(decision) if decision.exists() else {}
        if prior:
            require(prior["review_sha256"]==key,"Prior decision hash mismatch")
        short = prior.get("owner_review",{})
        records.append(dict(study=previous["active_path"],objective=previous.get("objective"),
                            result=doc.get("result"),retained=doc.get("retained_both"),
                            capability=short.get("capability"),lineages=short.get("lineage_count"),
                            notice=short.get("notice") or (prior.get("decision") or "")[:380],banks=banks))
        item=previous
    # Recent mechanistic detours can be several studies away from the last breeding comparison.
    def topics(text):
        return set(re.findall(r"[a-z]+",text.lower()))-{
            "does","the","with","than","from","and","that","after","over","study","target","targets","r","v"}
    words=topics(active.get("objective","")+" "+active["active_path"])
    related=sorted(range(len(records)),key=lambda i:
                   len(words & topics((records[i]["objective"] or "")+" "+records[i]["study"])),reverse=True)[:4]
    return [r if i<4 or i in related else {k:v for k,v in r.items() if k!="banks"}
            for i,r in enumerate(records[:100])]


def context(root,state,active,packet):
    work=state.parent
    handoff=(work/"fledge-monitor/RESEARCH_HANDOFF.md").read_text(encoding="utf-8")
    plan=inside(root,active["proposal_path"])
    raw=plan.read_bytes()
    require(digest(raw)==active["proposal_sha256"],"Proposal changed during review")
    return dict(completion=packet,registered_plan=raw.decode("utf-8-sig")[:20000],
                latest_handoff=handoff[:6500],recent=history(root,state,active))


def check_decision(d):
    require(isinstance(d,dict) and d.get("action") in ACTIONS,"Unsupported owner action")
    for key in ("decision","notice","capability","limiter","history_decision","architecture","successor_question"):
        require(isinstance(d.get(key),str) and d[key].strip(), "Incomplete review: "+key)
    require(len(d["notice"])<=500 and len(d["successor_question"])<=500 and type(d.get("lineage_count")) is int and d["lineage_count"]>=0
            and type(d.get("milestone")) is bool, "Invalid review summary or lineage count")
    outcomes=d.get("outcome_actions")
    require(isinstance(outcomes,list) and len(outcomes)>=2 and all(isinstance(x,dict) and
            all(isinstance(x.get(k),str) and x[k].strip() for k in ("outcome","action")) for x in outcomes)
            and len({x["action"].strip().casefold() for x in outcomes})>=2,"Outcomes must change the next action")


def interpret(root,state,active,packet,folder,existing=None):
    evidence=context(root,state,active,packet)
    if existing:
        evidence["previously_sealed_decision"]=existing["decision"]
    request={"review_sha256":packet["review_sha256"],"evidence":evidence}
    save(folder/"owner-review-request.json",request)
    instruction=(state.parent/POLICY).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        raw=codex(Path(temp),instruction+"\n"+FORMAT+"\nData only:\n"+json.dumps(evidence),"read-only")
    try:
        value=json.loads(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$","",raw.strip()))
        check_decision(value)
    except (ValueError,TypeError,Integrity) as e:
        raise Retry("Research review needs a complete structured decision: "+str(e)[:160]) from e
    return dict(review_sha256=packet["review_sha256"],decision=value["decision"],owner_review=value,recorded_at=now())


def publish_decision(state,active,record):
    """One handoff entry per sealed review; retry repairs a partially written notice."""
    key=record["review_sha256"]
    d=record["owner_review"]
    study=active["active_path"].split("/")[-1]
    data=dict(study=study,review_sha256=key,notice=d["notice"],question=d["successor_question"],
              action=d["action"],recorded_at=record["recorded_at"])
    handoff=state.parent/"fledge-monitor/RESEARCH_HANDOFF.md"
    tag="<!-- owner-review "+key+" -->"
    original=handoff.read_text(encoding="utf-8")
    if tag not in original:
        title,rest=original.split("\n",1)
        section=(f"\n\n{tag}\n## {study}\n\n{d['notice']}\n\n"
                 f"{d['successor_question']} [Decision](../fledge-coordinator-state/{key}/"
                 f"{'owner-decision.json' if (state/key/'owner-decision.json').exists() else 'decision.json'})\n")
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=handoff.parent,delete=False) as f:
            f.write(title+section+rest)
        Path(f.name).replace(handoff)
    for path in (state/"latest-decision.json",state/key/"notice.json"):
        if not path.exists() or read(path)!=data:
            save(path,data)


def preserve_milestone(state,record):
    from fledge_milestone import ensure_milestone, BoundaryChanged
    latest=state.parent/"fledge-milestones/latest.json"
    if latest.exists() and not record["owner_review"]["milestone"]:
        receipt=read(latest)
        verified=datetime.fromisoformat(receipt["restore"]["verified_at"])
        if (datetime.now(timezone.utc)-verified).total_seconds()<86400:
            return
    try:
        ensure_milestone(state.parent)
    except BoundaryChanged as exc:
        raise Retry(str(exc)) from exc


def record_handoff(root,state,active):
    """Use worker-written UTC instants, even on recovery after both banks finish."""
    child=inside(root,active["active_path"])
    plan=read(child/"preregistration.json")
    parent=checked(inside(root,plan["previous_review"]),plan["previous_review_sha256"])
    key=plan["prior_diagnostic_review_sha256"]
    require(active["active_path"]==plan["active_path"] and parent["review_sha256"]==key and
            parent["active_path"]==plan["parent_path"],"Timing receipt ancestry mismatch")
    checked(inside(root,parent["review_path"]),key)
    decision=read(state/key/"decision.json")
    require(decision["review_sha256"]==key,"Missing reviewed predecessor at registration")
    completed,started=map(datetime.fromisoformat,(parent["completed_at"],plan["created_at"]))
    reviewed=datetime.fromisoformat(decision["recorded_at"])
    require(completed<=reviewed<=started,"Completion, decision and registration times disagree")
    receipt=dict(completed_study=parent["active_path"],completed_at=parent["completed_at"],
                 reviewed_at=decision["recorded_at"],started_study=plan["active_path"],registered_at=plan["created_at"],
                 elapsed_seconds=round((started-completed).total_seconds(),3))
    path=state/key/"successor-start.json"
    if path.exists():
        require(read(path)==receipt,"Successor timing receipt changed")
    else:
        save(path,receipt)
    handoff=state.parent/"fledge-monitor/RESEARCH_HANDOFF.md"
    marker="<!-- owner-start "+key+" -->"
    original=handoff.read_text(encoding="utf8")
    if marker not in original:
        title,rest=original.split("\n",1)
        entry=(f"\n\n{marker}\nThe guarded worker registered `{Path(plan['active_path']).name}` at {started.isoformat()} "
               f"after review of `{Path(parent['active_path']).name}`. Completion to registration took "
               f"{receipt['elapsed_seconds']:g} seconds. [Timing receipt](../fledge-coordinator-state/{key}/successor-start.json)\n")
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf8",dir=handoff.parent,delete=False) as f:
            f.write(title+entry+rest)
        Path(f.name).replace(handoff)
    return receipt


def record_blocker(state,active,reason,*,integrity=False):
    """Keep private diagnostics local and report a persistent blocker once per boundary."""
    key=active.get("review_sha256") or active.get("prior_diagnostic_review_sha256")
    if not key or not re.fullmatch("[0-9a-f]{64}",key):
        return
    folder=state/key
    path=folder/"blocker.json"
    previous=read(path) if path.exists() else {}
    count=previous.get("attempts",0)+1 if previous.get("reason")==reason else 1
    binding=active["active_path"].split("/")[-1]
    record=dict(study=binding,attempts=count,reason=reason,integrity=integrity,observed_at=now())
    save(path,record)
    if not integrity and count<3:
        return
    short=("Integrity verification stopped this study. Inspect its local blocker receipt before resuming."
           if integrity else "The owner is blocked after repeated recovery attempts. See the local blocker receipt.")
    incident="blocker-"+digest((key+reason).encode())[:16]+".json"
    marker="<!-- owner-blocker "+incident+" -->"
    handoff=state.parent/"fledge-monitor/RESEARCH_HANDOFF.md"
    original=handoff.read_text(encoding="utf8")
    if marker not in original:
        title,rest=original.split("\n",1)
        save(folder/incident,record)
        entry=f"\n\n{marker}\n## {binding}\n\n{short} [Blocker](../fledge-coordinator-state/{key}/{incident})\n"
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf8",dir=handoff.parent,delete=False) as f:
            f.write(title+entry+rest)
        Path(f.name).replace(handoff)

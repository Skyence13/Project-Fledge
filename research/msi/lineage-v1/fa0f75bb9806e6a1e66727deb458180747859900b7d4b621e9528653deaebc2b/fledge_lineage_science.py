"""Matched response curves and selection experiments on a frozen descendant family."""
import math
from random import Random
from statistics import fmean, pstdev, quantiles

import fledge_blank_breeding as previous
from fledge_architecture import measure_generation, topology
from wisp.core import Genome, MOTORS, Wisp, mutate
from wisp.experiment import derived_seed, identifier, PROBE_ROUNDS
from wisp.pan_review import fresh_skills

DIAGNOSTIC = "finalist-blank-response-curve-v1"
BREEDING = "mechanism-guided-descendant-v1"
LEVELS = (-.8, -.4, 0., .4, .8)
TRANSFER = (-.6, -.2, .2, .6)
LIFETIMES = {"validation":32, "confirmation":64}
GENS = 3
POP = 8
TRAIN = 3
MIN_EFFECT = .03


def response(endpoint, contrast):
    """Exact native probability on the common dark frame after the signed unit-brightness cue."""
    clone = Wisp.restore(endpoint)
    clone.choose_levels((1-contrast)/2,(1+contrast)/2)
    clone.choose_levels(0.,0.)
    act = {n.id:value for n,value in zip(clone.genome.nodes,clone.checkpoint()["activation"],strict=True)}
    return 1/(1+math.exp(-2.2*(act[MOTORS[1]]-act[MOTORS[0]])))


def components(curve):
    neutral = curve[0.]
    return {"neutral_bias":neutral, "right_sensitivity":fmean((curve[.4],curve[.8]))-neutral,
            "left_sensitivity":neutral-fmean((curve[-.4],curve[-.8]))}


def contrast(left, right, seed):
    differences = [a-b for a,b in zip(left,right,strict=True)]
    rng = Random(seed)
    draws = [fmean(rng.choices(differences,k=len(differences))) for _ in range(2000)]
    cuts = quantiles(draws,n=40,method="inclusive")
    return {"difference":fmean(differences),"ci95":[cuts[0],cuts[-1]],"lifetimes":len(left),"draws":2000}


def meets(effect, floor):
    return effect["difference"] >= floor and effect["ci95"][0] > 0


def mechanism(rows):
    def replicated(key):
        return all(meets(r["mechanism_contrasts"][key],MIN_EFFECT) for r in rows.values())
    bias = replicated("neutral_bias")
    sensitivity = replicated("right_sensitivity")
    if bias and sensitivity:
        return "both_components_replicated"
    if bias:
        return "rightward_bias_replicated"
    if sensitivity:
        return "rightward_sensitivity_replicated"
    return "response_components_unresolved"


def all_old_rates(bank):
    metrics = bank["metrics"]
    return {"mixed_recall":metrics["mixed_recall"]["accuracy"],
            **{k:v["accuracy"] for k,v in metrics["old_skills"].items()},
            **{f"{phase}_{lag}":value["accuracy"] for phase in ("fixed_recall","fixed_one_shot")
               for lag,value in metrics[phase].items()}}


def evaluate(members, split, seeds, mode, guard=lambda:None):
    """Each member gets identical fresh target and action streams; scoring never modifies its genome."""
    genomes = {name:Genome.from_dict(member["genome"]) for name,member in members.items()}
    previous.require(all(identifier(g)==members[k]["fingerprint"] for k,g in genomes.items()),"Member mismatch")
    n = LIFETIMES[split]
    bank_seed = derived_seed(seeds[split],mode,split)
    earlier = seeds[split+"_earlier"]
    samples = {arm:[] for arm in (*genomes,"last_visible_decoder")}
    curves = {arm:[] for arm in genomes}
    old = {arm:[] for arm in genomes}
    for i in range(n):
        guard()
        counts,endpoints = previous.one_lifetime(genomes,bank_seed,i)
        for arm,values in counts.items():
            samples[arm].append(values)
        for arm,agent in endpoints.items():
            state = agent.checkpoint()
            curves[arm].append({level:response(state,level) for level in (*LEVELS,*TRANSFER)})
            old[arm].append(previous.endpoint_skills(agent,derived_seed(earlier,mode,split,i,"post")))
            previous.require(agent.checkpoint()==state,"Probing altered practice endpoint")
    held = {a:previous.average_cells(rows) for a,rows in samples.items()}
    curve_scores = {a:{str(k):{"p_right":fmean(r[k] for r in rows),"lifetime_sd":pstdev(r[k] for r in rows)}
                       for k in LEVELS} for a,rows in curves.items()}
    features = {a:[components(r) for r in rows] for a,rows in curves.items()}
    target = "finalist"
    baseline = "frozen_ancestor"
    stream = derived_seed(bank_seed,"paired_interval")
    mechanism_diffs = {k:contrast([r[k] for r in features[target]], [r[k] for r in features[baseline]], stream)
                       for k in features[target][0]}
    def binary_contrasts(other):
        return {cell:contrast([r[cell]/previous.cell_budget(cell) for r in samples[target]],
                              [r[cell]/previous.cell_budget(cell) for r in samples[other]],stream)
                for cell in ("left_target","right_target","blank","visible")}
    comparisons = {a:binary_contrasts(a) for a in members if a!=target}
    # Unseen cue strengths; expected accuracy under each receiver's native stochastic policy.
    transfer = {a:{side:[fmean((1-r[k]) if k<0 else r[k] for k in TRANSFER if (k<0)==(side=="left"))
                              for r in rows] for side in ("left","right")} for a,rows in curves.items()}
    transfer_contrasts = {side:contrast(transfer[target][side],transfer[baseline][side],stream) for side in ("left","right")}
    fresh = {a:fresh_skills(m,earlier,split) for a,m in members.items()}
    fresh_delta = {k:v-all_old_rates(fresh[baseline])[k] for k,v in all_old_rates(fresh[target]).items()}
    post = {a:{phase:fmean(r[phase] for r in rows) for phase in ("recall","reversal")} for a,rows in old.items()}
    post_delta = {k:v-post[baseline][k] for k,v in post[target].items()}
    post_pass = min(post_delta.values()) >= -.05-1e-12
    retained = fresh[target]["passed"] and min(fresh_delta.values()) >= -.05-1e-12 and post_pass
    gates = {"instrumentation":True,"both_blank_sides":all(meets(comparisons[baseline][k],.05) for k in ("left_target","right_target")),
             "visible":held[target]["visible"]["accuracy"]>=.75 and comparisons[baseline]["visible"]["difference"]>=-.05,
             "older_skills":retained, "transfer_both_sides":all(meets(v,.03) for v in transfer_contrasts.values())}
    if mode==BREEDING:
        gates["beats_matched_selection"] = all(meets(comparisons["matched_selection"][k],.03) for k in ("left_target","right_target"))
        gates["improves_over_parent"] = all(meets(comparisons["parent_finalist"][k],.03) for k in ("left_target","right_target"))
    return {"split":split,"lifetimes":n,"held_out":held,"right_choice_curve":curve_scores,
            "mechanism_contrasts":mechanism_diffs,"visible_contrasts":comparisons[baseline],"matched_contrasts":comparisons,
            "transfer":{"levels":list(TRANSFER),"expected_accuracy":{a:{s:fmean(v) for s,v in x.items()} for a,x in transfer.items()},
                        "contrasts":transfer_contrasts},
            "fresh_skills":fresh[target],"ancestor_fresh_skills":fresh[baseline],"fresh_minus_ancestor":fresh_delta,
            "post_practice":{"passed":post_pass,"rates":post,"difference":post_delta,"body_lifetime_pairs":n,
                             "probes_per_phase_per_arm":n*4*PROBE_ROUNDS},
            "size":{a:{"neurons":len(g.nodes),"connections":len(g.edges)} for a,g in genomes.items()},
            "architecture":{a:topology(g) for a,g in genomes.items()},
            "fingerprints":{a:m["fingerprint"] for a,m in members.items()},"instrumentation_passed":True,
            "retained":bool(retained),"gates":gates,"passed":all(gates.values())}


def ranking(row, branch):
    left,right,visible = (row[k] for k in ("left_target","right_target","visible"))
    if branch=="baseline":
        return (min(left,right), (left+right)/2,visible)
    if isinstance(branch,dict):
        values = dict(row,worst_blank_side=min(left,right),balanced_blank=(left+right)/2,
                      neutral_displacement=abs(row["neutral"]-.5),weakest_sensitivity=min(row["left_evidence"],row["right_evidence"]))
        return (sum(values[k]*w for k,w in branch["coefficients"].items()),visible,(left+right)/2)
    # Use training-only continuous scores to limit ties. Both arms have identical workload.
    neutral = abs(row["neutral"]-.5)
    weakest = min(row["left_evidence"],row["right_evidence"])
    objective = {"rightward_bias_replicated":-neutral,
                 "rightward_sensitivity_replicated":weakest,
                 "both_components_replicated":weakest-neutral}[branch]
    return (min(left,right)+objective/2, visible, (left+right)/2)


def generation(members, index, branch, training_seed, guard=lambda:None):
    rows = []
    seed = derived_seed(training_seed,"selection",index)
    for slot,member in enumerate(members):
        guard()
        genome = Genome.from_dict(member["genome"])
        previous.require(member["fingerprint"]==identifier(genome),"Mutated training record")
        examples = []
        for life in range(TRAIN):
            counts,agents = previous.one_lifetime({"candidate":genome},seed,life)
            point = {k:counts["candidate"][k]/previous.cell_budget(k) for k in ("blank","visible","left_target","right_target")}
            state = agents["candidate"].checkpoint()
            curves = {k:response(state,k) for k in LEVELS}
            c = components(curves)
            examples.append(dict(point,neutral=c["neutral_bias"],left_evidence=c["left_sensitivity"],right_evidence=c["right_sensitivity"]))
        means = {k:fmean(r[k] for r in examples) for k in examples[0]}
        rows.append(dict(means,slot=slot,fingerprint=member["fingerprint"],parent_fingerprint=member["parent_fingerprint"],
                         neurons=len(genome.nodes),connections=len(genome.edges)))
    ranked = sorted(rows,key=lambda r:ranking(r,branch),reverse=True)
    best = members[ranked[0]["slot"]]
    if index==GENS:
        descendants = [r for r in ranked if members[r["slot"]]["fingerprint"]!=members[r["slot"]]["base_fingerprint"]]
        previous.require(bool(descendants),"No changed descendant to compare")
        best = members[descendants[0]["slot"]]
    population = {"count":len(rows),"mean_accuracy":fmean(r["blank"] for r in rows),"accuracy_sd":pstdev(r["blank"] for r in rows),
                  "mean_neurons":fmean(r["neurons"] for r in rows),"mean_connections":fmean(r["connections"] for r in rows)}
    children = []
    measured_children=[]
    if index<GENS:
        children.append(best)
        for slot in range(1,POP):
            draw = derived_seed(training_seed,"breed",index+1,slot)
            parent_slot = Random(draw).choice(ranked[:2])["slot"]
            parent = members[parent_slot]
            mutant = mutate(Genome.from_dict(parent["genome"]),Random(derived_seed(draw,"mutation")),
                            rules=True,wiring=True,evolve_trace=True)
            previous.require(len(mutant.nodes)<=12,"Registered capacity exceeded")
            children.append({"genome":mutant.to_dict(),"fingerprint":identifier(mutant),
                             "parent_fingerprint":parent["fingerprint"],"base_fingerprint":parent["base_fingerprint"]})
            measured_children.append(dict(group="population",parent_slot=parent_slot,birth_generation=index+1,genome=mutant.to_dict()))
    population["architecture"]=measure_generation([dict(m,group="population",slot=i) for i,m in enumerate(members)],
        {"members":[dict(r,group="population") for r in rows],"generation":index},
        measured_children if index<GENS else None,{"population":True},lambda row:ranking(row,branch))
    return {"selected":best, "population":population, "members":rows,"next_members":children}

"""Keep only approved research source in an append-only MSI Git archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


BASE = "c3035d31e4636c6c05991e3e37b2439013b039b2"
BRANCH = "research/msi-owner"
REF = "refs/heads/" + BRANCH
REMOTE = "https://github.com/Skyence13/Project-Fledge.git"
STATE_SWAP = "2609e7dbce91bd521523e25790a9130c80388d02"
PACKAGES = {
    "breeding-v1": ("fledge_blank_breeding.py", "fledge_breeding_worker.py",
                    "bounded_evaluation_worker.py", "test_fledge_blank_breeding.py", "fledge_breeding_proposal.md"),
    "lineage-v1": ("fledge_lineage_worker.py", "fledge_lineage_science.py", "test_fledge_lineage.py",
                   "fledge_lineage_protocol.md", "fledge_blank_breeding.py", "bounded_evaluation_worker.py",
                   "fledge_prepare.py", "fledge_generic_worker.py", "fledge_generated.py", "fledge_architecture.py"),
    "architecture-v1": ("fledge_architecture.py", "test_fledge_architecture.py"),
    "generated-v1": ("wisp/state_swap.py","wisp/generated_study.py","tests/test_generated_study.py",
                     "state-swap.commit","candidate.commit"),
    "owner-v1": ("fledge_coordinator.py", "fledge_prepare.py", "fledge_generated.py", "fledge_generic_worker.py",
                 "fledge_source_archive.py", "install_fledge_coordinator.py", "Install-FledgeCoordinator.cmd",
                 "fledge_events.py", "test_fledge_coordinator.py", "test_install_fledge_coordinator.py",
                 "test_fledge_source_archive.py", "fledge_research_owner.py", "test_fledge_research_owner.py",
                 "test_fledge_generated.py", "bounded_evaluation_worker.py", "source_review_worker.py",
                 "fledge_breeding_worker.py", "fledge_milestone.py", "test_fledge_milestone.py",
                 "fledge_completion_watch.py", "test_bounded_evaluation_worker.py",
                 "fledge-monitor/RESEARCH_OWNER.md"),
    "monitor-v1": ("aggregate_cache.py", "local_monitor.py", "test_aggregate_cache.py",
                   "ui/app.js", "ui/style.css", "ui/index.html", "worker.mjs", "local-reader/wisp/dashboard_summary.py",
                   "local-reader/tests/test_dashboard_summary.py", "relay.py", "Start-Fledge-Monitor.cmd", "build.mjs"),
}
# Keep older published layouts valid when a protocol acquires a new source dependency.
# New packages must always use the current exact layout in PACKAGES.
LEGACY_LAYOUTS = {"lineage-v1": [frozenset(PACKAGES["lineage-v1"])-{"fledge_architecture.py"}]}
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args, data=None, env=None):
    return subprocess.run(["git", "-c", "safe.directory=" + Path(repo).resolve().as_posix(),
                           "-C", str(repo), *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, check=True, timeout=60).stdout


def initialize(repository, reference):
    """Create from a single known public research commit; never import workstation refs."""
    repo = Path(repository)
    if not repo.exists():
        pending = repo.with_name(repo.name+"-initializing-"+uuid.uuid4().hex)
        pending.mkdir()
        try:
            git(pending, "init", "--bare")
            git(pending, "fetch", "--depth=1", "--no-tags", str(Path(reference).resolve()), BASE)
            git(pending, "update-ref", REF, BASE)
            git(pending, "symbolic-ref", "HEAD", REF)
            git(pending, "config", "remote.origin.url", REMOTE)
            try:
                pending.rename(repo)
            except OSError:
                if not repo.is_dir():
                    raise
        finally:
            if pending.exists():
                shutil.rmtree(pending)
    if git(repo, "rev-parse", "--is-bare-repository").strip() != b"true":
        raise ValueError("Expected dedicated bare source archive")
    git(repo, "merge-base", "--is-ancestor", BASE, REF)


def read_sources(work, package, pins):
    names = PACKAGES[package]
    if set(pins) != set(names):
        raise ValueError("Source package allowlist mismatch")
    root = Path(work).resolve(strict=True)
    files = {}
    for name in names:
        path = root / name
        if path.is_symlink() or path.resolve(strict=True) != path or not path.is_file() or path.stat().st_size > 180000:
            raise ValueError("Invalid source file " + name)
        content = path.read_bytes()
        if not content or not HEX64.fullmatch(pins[name]) or sha(content) != pins[name]:
            raise ValueError("Source digest differs for " + name)
        check_public_source(name,content)
        files[name] = content
    return files


def check_public_source(name, content):
    source = content.decode("utf-8-sig")
    # Exclude binary payloads, credentials, user-specific paths and embedded raw genomes.
    sensitive = (r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}"
                 r"|sk-[A-Za-z0-9_-]{28,}|https?://[^\s/:]+:[^\s/@]+@|[A-Z]:[\\/]Users[\\/][^\s]+"
                 r"|[a-zA-Z0-9+/=]{150,}|[\"']nodes[\"']\s*:\s*\[\s*\{\s*[\"'](?:id|leak)[\"']")
    if not content or len(content)>180000 or "\x00" in source or re.search(sensitive,source):
        raise ValueError("Source content requires privacy review: " + name)


def archive_source(work, repository, package, pins, reference_revision, reference_tree, *, reconstructed=False):
    if not all(HEX40.fullmatch(x) for x in (reference_revision, reference_tree)):
        raise ValueError("Invalid reference source identifiers")
    files = read_sources(work, package, pins)
    mode = "reconstructed_from_registered_hashes" if reconstructed else "pinned_before_execution"
    if package == "generated-v1":
        mode = "recovered_tested_git_commit" if reconstructed else "tested_git_commit"
    if package in {"owner-v1","monitor-v1","architecture-v1"}:
        mode = "verified_source_snapshot" if not reconstructed else "reconstructed_from_registered_hashes"
    manifest = dict(schema=1, package=package, provenance=mode, reference_revision=reference_revision,
                    reference_tree=reference_tree, source_sha256=dict(pins))
    metadata = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    prefix = f"research/msi/{package}/{sha(metadata)}"
    files["source-manifest.json"] = metadata
    repo = Path(repository).resolve()
    initialize(repo, repo.parent / "fledge-one-blank-pr13")
    for _ in range(5):
        parent = git(repo, "rev-parse", REF).decode().strip()
        previous = git(repo, "log", "-1", "--format=%H", parent, "^"+BASE, "--", prefix).decode().strip()
        if previous:
            commit = previous
            break
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / "index"))
            git(repo, "read-tree", parent, env=env)
            for name, content in files.items():
                blob = git(repo, "hash-object", "-w", "--stdin", data=content).decode().strip()
                git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{prefix}/{name}", env=env)
            tree = git(repo, "write-tree", env=env).decode().strip()
            commit = git(repo, "-c", "user.name=Fledge MSI", "-c", "user.email=fledge@localhost", "commit-tree",
                         tree, "-p", parent, "-m", f"Archive {package} source ({mode})").decode().strip()
        try:
            git(repo, "update-ref", REF, commit, parent)
            break
        except subprocess.CalledProcessError:
            continue
    else:
        raise RuntimeError("Concurrent source archival did not settle")
    record = dict(manifest, archive_commit=commit, archive_tree=git(repo, "rev-parse", commit+"^{tree}").decode().strip(),
                  archive_path=prefix, execution_commit=execution_id(mode, commit, reference_revision))
    verify_source(work, repository, record)
    return record


def verify_source(work, repository, record):
    """Call again while holding the worker launch locks."""
    read_sources(work, record["package"], record["source_sha256"])
    verify_receipt(repository, record)


def verify_receipt(repository, record):
    """Verify an immutable execution receipt even when today's working files have advanced."""
    pins = record["source_sha256"]
    allowed = [frozenset(PACKAGES[record["package"]]), *LEGACY_LAYOUTS.get(record["package"],())]
    if frozenset(pins) not in allowed or record["schema"] != 1 or not all(
            HEX40.fullmatch(record[k]) for k in ("archive_commit","archive_tree","reference_revision","reference_tree")):
        raise ValueError("Invalid source receipt")
    commit = record["archive_commit"]
    if not HEX40.fullmatch(commit) or git(repository, "rev-parse", commit+"^{tree}").decode().strip() != record["archive_tree"]:
        raise ValueError("Source archive tree differs")
    git(repository, "merge-base", "--is-ancestor", commit, REF)
    mode = record["provenance"]
    if record["execution_commit"] != execution_id(mode, commit, record["reference_revision"]):
        raise ValueError("Source execution provenance differs")
    manifest = {k: record[k] for k in ("schema","package","provenance","reference_revision","reference_tree","source_sha256")}
    metadata = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    prefix = f"research/msi/{record['package']}/{sha(metadata)}"
    if prefix != record["archive_path"]:
        raise ValueError("Source package path differs")
    names = set(git(repository,"ls-tree","-r","--name-only",commit+":"+prefix).decode().splitlines())
    if names != set(pins) | {"source-manifest.json"}:
        raise ValueError("Source archive contains unregistered entries")
    for name, expected in {**pins, "source-manifest.json":sha(metadata)}.items():
        if sha(git(repository, "cat-file", "blob", commit+":"+prefix+"/"+name)) != expected:
            raise ValueError("Archived source differs: " + name)
    if record["package"] == "generated-v1":
        verify_generated_objects(repository,record)


def execution_id(mode, commit, reference):
    if mode in {"tested_git_commit","recovered_tested_git_commit"}:
        return reference
    if mode == "pinned_before_execution":
        return commit
    if mode in {"reconstructed_from_registered_hashes","verified_source_snapshot"}:
        return None
    raise ValueError("Unknown source provenance")


def verify_generated_objects(repository, record):
    """Rebuild both exact executed trees and commit objects, from the source-only receipt."""
    prefix = record["archive_commit"]+":"+record["archive_path"]+"/"
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp)/"index"))
        git(repository,"read-tree",BASE,env=env)
        parent = BASE
        for label, expected, files in (("state-swap",STATE_SWAP,("wisp/state_swap.py",)),
                    ("candidate",record["reference_revision"],("wisp/generated_study.py","tests/test_generated_study.py"))):
            for name in files:
                blob = git(repository,"rev-parse",prefix+name).decode().strip()
                git(repository,"update-index","--add","--cacheinfo",f"100644,{blob},{name}",env=env)
            tree = git(repository,"write-tree",env=env).decode().strip()
            raw = git(repository,"cat-file","blob",prefix+label+".commit")
            if not raw.startswith(f"tree {tree}\nparent {parent}\nauthor ".encode()) or git(
                    repository,"hash-object","-t","commit","--stdin",data=raw).decode().strip() != expected:
                raise ValueError("Executed Git objects differ from archived source")
            parent = expected
        if tree != record["reference_tree"]:
            raise ValueError("Generated execution tree changed")


def archive_generated(code, repository, revision, tree, *, recovered=False):
    """Retain the executable Git objects as text and code, with only two candidate additions."""
    if not HEX40.fullmatch(revision) or not HEX40.fullmatch(tree):
        raise ValueError("Bad generated Git identifiers")
    if (git(code,"rev-parse","HEAD").decode().strip() != revision or git(code,"rev-parse","HEAD^{tree}").decode().strip()!=tree
            or git(code,"rev-parse","HEAD^").decode().strip() != STATE_SWAP or git(code,"status","--porcelain")):
        raise ValueError("Candidate source no longer matches registered checkout")
    expected_paths = {"A\twisp/generated_study.py","A\ttests/test_generated_study.py"}
    if set(git(code,"diff","--name-status",STATE_SWAP,revision).decode().splitlines()) != expected_paths:
        raise ValueError("Unexpected generated code or data file")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for name in PACKAGES["generated-v1"]:
            path = root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            if name in {"candidate.commit","state-swap.commit"}:
                sha1 = revision if name == "candidate.commit" else STATE_SWAP
                raw = git(code,"cat-file","commit",sha1)
            else:
                raw = git(code,"cat-file","blob",revision+":"+name)
            path.write_bytes(raw)
        pins = {name:sha((root/name).read_bytes()) for name in PACKAGES["generated-v1"]}
        return archive_source(root,repository,"generated-v1",pins,revision,tree,reconstructed=recovered)


def on_study_completion(work,state,active):
    """Called at a verified boundary. Record private linkage only in the review state directory."""
    root = Path(work)
    state = Path(state)
    repo = root/"fledge-source-history"
    parent = active["prior_diagnostic_review_sha256"]
    review = active["review_sha256"]
    child = active["active_path"]
    if not HEX64.fullmatch(parent) or not HEX64.fullmatch(review) or not re.fullmatch(r"continuations/[a-z0-9-]+",child):
        raise ValueError("Invalid completion identity")
    folder = state/review
    folder.mkdir(exist_ok=True)
    path = folder/"source-execution.json"
    registration = json.loads((root/"fledge/runs/personal.json.campaign"/child/"preregistration.json").read_bytes())
    if registration["research_revision"] != active["research_revision"] or registration["research_tree"] != active["research_tree"]:
        raise ValueError("Completed study and registration disagree on source")
    record = registration.get("source_execution")
    prepared = state/parent/"prepared.json"
    if path.exists():
        saved = json.loads(path.read_bytes())
        if saved["review_sha256"] != review or (record is not None and saved["source_execution"] != record):
            raise ValueError("Completion source receipt mismatch")
        record = saved["source_execution"]
    elif record is None and prepared.exists():
        prepared_data = json.loads(prepared.read_bytes())
        if prepared_data.get("kind") == "generated":
            p = prepared_data
            if (p.get("child") != child or p.get("parent") != parent or p.get("revision") != active["research_revision"]
                    or p.get("tree") != active["research_tree"] or not re.fullmatch(r"candidate-[A-Za-z0-9_-]+",p["directory"])):
                raise ValueError("Completion did not match generated source preparation")
            code = prepared.parent/p["directory"]/"code"
            if code.resolve(strict=True) != code.absolute():
                raise ValueError("Generated source checkout redirected")
            record = archive_generated(code,repo,p["revision"],p["tree"],recovered=True)
    if record is None and child == "continuations/one-blank-stationary-breeding-r5":
        record = archive_source(root,repo,"breeding-v1",registration["implementation_sha256"],
                                active["research_revision"],active["research_tree"],reconstructed=True)
    if record is not None:
        verify_receipt(repo,record)
        if record["reference_revision"] != active["research_revision"] or record["reference_tree"] != active["research_tree"]:
            raise ValueError("Receipt points to a different execution reference")
        if not path.exists():
            atomic_json(path,{"review_sha256":review,"source_execution":record})
    on_completion(work,state)
    return record


def publish(repository):
    """Fast-forward only. Both sides must descend from the designated source branch."""
    repo = Path(repository)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    if git(repo, "config", "--get", "remote.origin.url").decode().strip() != REMOTE:
        raise ValueError("Unexpected source remote")
    git(repo, "fetch", "--no-tags", REMOTE, REF, env=env)
    remote = git(repo, "rev-parse", "FETCH_HEAD").decode().strip()
    local = git(repo, "rev-parse", REF).decode().strip()
    verify_publish_tree(repo, local)
    git(repo, "merge-base", "--is-ancestor", BASE, remote)
    if git(repo, "rev-parse", remote+"^{tree}") == git(repo, "rev-parse", local+"^{tree}"):
        return local, remote
    # The connector can mirror an identical tree with a different GitHub commit identity.
    # Join only when the complete prior remote tree matches a previously approved local archive.
    history = git(repo, "log", "--format=%T", local, "^"+BASE).decode().splitlines()
    remote_tree = git(repo, "rev-parse", remote+"^{tree}").decode().strip()
    if remote != BASE and remote_tree not in history:
        raise ValueError("Remote source tree changed independently")
    try:
        git(repo,"merge-base","--is-ancestor",remote,local)
        merged = local
    except subprocess.CalledProcessError:
        merged = git(repo, "-c", "user.name=Fledge MSI", "-c", "user.email=fledge@localhost", "commit-tree",
                     git(repo, "rev-parse", local+"^{tree}").decode().strip(), "-p", remote, "-p", local,
                     "-m", "Publish verified MSI source archives").decode().strip()
    git(repo, "push", REMOTE, merged+":"+REF, env=env)
    if git(repo, "ls-remote", REMOTE, REF, env=env).decode().split()[0] != merged:
        raise RuntimeError("Remote source branch readback differs")
    return local, merged


def verify_publish_tree(repo, local):
    """External publication accepts only additive, manifest-verified source objects."""
    for commit in git(repo, "rev-list", local, "^"+BASE).decode().splitlines():
        changes = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", commit).decode().splitlines()
        names = [line.split("\t") for line in changes]
        if not names or any(len(e) != 2 or e[0] != "A" for e in names):
            raise ValueError("Only append-only source commits may be published")
        manifests = [n for _,n in names if re.fullmatch(r"research/msi/[a-z0-9-]+/[a-f0-9]{64}/source-manifest.json",n)]
        if len(manifests) != 1:
            raise ValueError("Expected one approved source manifest per commit")
        prefix, _ = manifests[0].rsplit("/",1)
        manifest = json.loads(git(repo,"cat-file","blob",commit+":"+manifests[0]))
        if {entry[1] for entry in names} != {prefix+"/"+n for n in (*manifest["source_sha256"],"source-manifest.json")}:
            raise ValueError("Commit contains files outside source allowlist")
        record = dict(manifest, archive_commit=commit, archive_path=prefix,
                      archive_tree=git(repo,"rev-parse",commit+"^{tree}").decode().strip(),
                      execution_commit=execution_id(manifest["provenance"],commit,manifest["reference_revision"]))
        verify_receipt(repo,record)
        for name in manifest["source_sha256"]:
            check_public_source(name,git(repo,"cat-file","blob",commit+":"+prefix+"/"+name))


def atomic_json(path, record):
    temporary = path.with_name(uuid.uuid4().hex+".tmp")
    try:
        temporary.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def on_completion(work, state, record=None, *, start_publisher=True):
    """Keep registrations local. Queue publication by source ref only and return immediately."""
    repo = Path(work)/"fledge-source-history"
    if record is not None:
        verify_receipt(repo, record)
    if not repo.exists():
        return
    directory = Path(state)/"source-publication"
    directory.mkdir(parents=True, exist_ok=True)
    head = git(repo,"rev-parse",REF).decode().strip()
    ack = directory/"delivered.json"
    if ack.exists() and json.loads(ack.read_bytes()).get("local_commit") == head:
        return
    if not (directory/(head+".pending")).exists():
        (directory/(head+".pending")).touch()
    attempt = directory/"attempt.json"
    if start_publisher and (not attempt.exists() or time.time() - attempt.stat().st_mtime > 300):
        subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "drain", "--repository", str(repo),
                          "--state", str(state)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))


def drain(repo, state):
    directory = Path(state)/"source-publication"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/"publish.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                return
        else:
            import fcntl
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
        attempt = directory/"attempt.json"
        if attempt.exists() and time.time() - attempt.stat().st_mtime < 300:
            return
        atomic_json(attempt, {"state":"publishing", "time":time.time()})
        try:
            local, remote = publish(repo)
        except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as error:
            atomic_json(attempt, {"state":"retry_pending", "failure":type(error).__name__, "time":time.time()})
            return
        acknowledge(repo, directory, local, remote)


def acknowledge(repo, directory, local, remote):
    """Record a CLI or independently verified connector readback."""
    if not HEX40.fullmatch(local) or not HEX40.fullmatch(remote):
        raise ValueError("Bad Git commit identifier")
    git(repo,"merge-base","--is-ancestor",local,REF)
    tree = git(repo,"rev-parse",local+"^{tree}").decode().strip()
    atomic_json(directory/"delivered.json", {"local_commit":local,"remote_commit":remote,"tree":tree,"time":time.time()})
    for pending in directory.glob("*.pending"):
        if HEX40.fullmatch(pending.stem):
            try:
                git(repo,"merge-base","--is-ancestor",pending.stem,local)
            except subprocess.CalledProcessError:
                continue
            pending.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["publish","drain"])
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parent/"fledge-source-history")
    parser.add_argument("--state", type=Path, default=Path(__file__).resolve().parent/"fledge-coordinator-state")
    args = parser.parse_args()
    if args.command == "drain":
        drain(args.repository,args.state)
    else:
        print(publish(args.repository))

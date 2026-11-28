"""Local content-addressed Fledge milestones and paused, isolated recovery rehearsals."""

import argparse
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from uuid import uuid4
from zipfile import ZipFile
import zlib

from fledge_completion_watch import try_lock

WORK = Path(__file__).resolve().parent
RUNS = Path("fledge/runs")
CAMP = RUNS / "personal.json.campaign"
OWNER = Path("fledge-coordinator-state")
BACKUPS = "fledge-milestones"
DAY = 86400


class BoundaryChanged(RuntimeError):
    """The scheduled owner may retry when a completed review and its decision are stable."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(uuid4().hex + ".tmp")
    try:
        with tmp.open("w", encoding="utf8") as f:
            json.dump(value, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def check(ok, message):
    if not ok:
        raise ValueError(message)


@contextmanager
def new_tree(parent, prefix):
    path = parent / (prefix + uuid4().hex[:12])
    path.mkdir()
    try:
        yield path
    finally:
        # Only an exclusively created immediate child of this backup directory.
        check(path.resolve().parent == parent.resolve() and not path.is_symlink(), "Unsafe temporary path")
        def writable_retry(function, filename, exc):
            if not isinstance(exc, PermissionError):
                raise exc
            os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
            function(filename)
        shutil.rmtree(path, onexc=writable_retry)


def safe(root, relative):
    name = str(relative)
    p = PurePosixPath(name)
    check(name and not PureWindowsPath(name).drive and not p.is_absolute() and "\\" not in name and
          all(part not in ("", ".", "..") and ":" not in part for part in name.split("/")), "Invalid backup path")
    result = (root / p).resolve()
    check(result.is_relative_to(root.resolve()) and result != root.resolve(), "Backup path outside root")
    return result


def git(repo, *args, **kwargs):
    repo = Path(str(repo).removeprefix("\\\\?\\"))
    command = ["git", "-c", "safe.directory=" + repo.resolve().as_posix(), "-c", "core.longpaths=true",
               "-c", "gc.auto=0", "-C", str(repo), *(str(a).removeprefix("\\\\?\\") for a in args)]
    return subprocess.run(command, check=True, stderr=subprocess.PIPE, stdout=kwargs.pop("stdout", subprocess.PIPE),
                          timeout=120, **kwargs).stdout


def boundary(work):
    active = read(work / CAMP / "active_research.json")
    parent = active.get("review_sha256", "")
    if not re.fullmatch("[0-9a-f]{64}", parent) or active.get("state") not in (
            "bounded_study_complete", "bounded_study_unscored"):
        raise BoundaryChanged("Awaiting completed review")
    decision = work / OWNER / parent / "decision.json"
    if not decision.is_file() or read(decision).get("review_sha256") != parent:
        raise BoundaryChanged("Awaiting recorded decision")
    return parent


def science_files(work):
    campaign = work / CAMP
    for path in sorted(campaign.rglob("*")):
        if path.is_file() and (path.suffix in {".sqlite3", ".json", ".md"} or path.name == "paused"):
            yield path
    for name in ("personal.json.lineages.sqlite3", "personal.json"):
        path = work / RUNS / name
        if path.exists():
            yield path
    for path in sorted((work / RUNS / "personal.json.next-plan").glob("*.json")):
        yield path
    for path in sorted((work / OWNER).glob("*/*.json")):
        if re.fullmatch("[0-9a-f]{64}", path.parent.name):
            yield path
    for path in sorted((work / "fledge-review-receipts").glob("*.json")):
        yield path
    for path in sorted((work / OWNER).glob("*.json")):
        # Store permits only as historical evidence under custody/, never at their operational paths.
        if path.name not in {"status.json", "last-event.json", "service-session.json"}:
            yield path


def frozen_index(work):
    return {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in science_files(work)}


def runtime_files(work):
    return sorted({*work.glob("*.py"), *work.glob("*proposal.md"), *work.glob("*Fledge*.cmd"),
                   *(work / "fledge/wisp").glob("*.py"),
                     *(p for p in (work / "fledge-monitor/RESEARCH_HANDOFF.md", work / "fledge-monitor/LAB_SYNC.md",
                                   work / "fledge-monitor/RESEARCH_OWNER.md", work / "fledge_lineage_protocol.md")
                     if p.is_file())})


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


class Collector:
    def __init__(self, work, store, stage):
        self.work, self.store, self.stage = work, store, stage
        self.files = {}
        self.gaps = []
        self.shallow = set()

    def remember_boundaries(self, repo):
        shallow = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "shallow").decode().strip())
        if shallow.is_file():
            hashes = shallow.read_text().splitlines()
            check(all(re.fullmatch("[0-9a-f]{40}", s) for s in hashes), "Invalid shallow Git boundary")
            self.shallow.update(hashes)

    def put(self, key, src):
        # Blobs have inherited MSI workspace permissions and are immutable after publication.
        h = digest(src)
        blob = self.store / "objects" / h[:2] / h
        blob.parent.mkdir(parents=True, exist_ok=True)
        if not blob.exists():
            tmp = blob.with_name(uuid4().hex + ".tmp")
            try:
                shutil.copyfile(src, tmp)
                if digest(tmp) != h:
                    raise BoundaryChanged("Source changed during backup")
                os.replace(tmp, blob)
            finally:
                tmp.unlink(missing_ok=True)
        self.files[key] = {"sha256": h, "size": src.stat().st_size}

    def sources(self, registrations):
        work = self.work
        for p in runtime_files(work):
            self.put("runtime/" + p.relative_to(work).as_posix(), p)
        by_revision = {}
        for p in (work / OWNER).glob("*/prepared.json"):
            m = read(p)
            if m.get("kind") == "generated":
                code = safe(p.parent, m["directory"]) / "code"
                by_revision[m["revision"]] = code
                for name, expected in m["pins"].items():
                    author = safe(code.parent / "author", name)
                    check(digest(author) == expected, "Historical author file differs: " + str(author))
                    self.put("prepared/" + p.parent.name + "/" + name, author)
        repos = [p for p in (work / "fledge", work / "fledge-one-blank-pr13", work / "fledge-state-swap",
                              work / "fledge-pan-draft", work / "fledge-stationary-pr12",
                              work / "fledge-source-history") if (p / ".git").exists()]
        recovered = self.store / "historical-source.git"
        if (recovered / "HEAD").is_file():
            repos.insert(0, recovered)
        evidence = self.store / "historical-input.json"
        if evidence.is_file():
            self.put("sources/github-historical-revisions.json", evidence)
        # Early workers recorded literal working-tree file hashes, including local newlines.
        # Keep exact evaluator bytes even when an old Git object isn't available locally.
        pins = {r["module_sha256"] for _, r in registrations if r.get("module_sha256")}
        module_sources = [*work.glob("*.py"), *work.glob("*/wisp/*.py"),
                          *(p / "wisp/generated_study.py" for p in by_revision.values())]
        pinned_files = {}
        for p in module_sources:
            if not p.is_file():
                continue
            raw = p.read_bytes()
            candidates = (raw, raw.replace(b"\r\n", b"\n"), raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            for data in candidates:
                h = sha(data)
                if h in pins and h not in pinned_files:
                    key = "evaluators/" + h + "/" + p.name
                    temp = self.stage / "module.py"
                    temp.write_bytes(data)
                    self.put(key, temp)
                    pinned_files[h] = key
        for leaf, r in registrations:
            if r.get("module_sha256") and r["module_sha256"] not in pinned_files:
                self.gaps.append(leaf + ": exact registered evaluator bytes unavailable")
            for name, expected in r.get("implementation_sha256", {}).items():
                saved = self.files.get("runtime/" + name, {})
                if saved.get("sha256") != expected:
                    self.gaps.append(leaf + ": historical implementation bytes unavailable for " + name)
        trees = {}
        for leaf, record in registrations:
            rev = record.get("research_revision", "")
            if not re.fullmatch("[0-9a-f]{40}", rev):
                self.gaps.append(leaf + ": source revision unrecorded")
                continue
            if rev not in trees:
                chosen = None
                for repo in ([by_revision[rev]] if rev in by_revision else []) + repos:
                    try:
                        tree = git(repo, "rev-parse", "--verify", rev + "^{tree}").decode().strip()
                    except subprocess.CalledProcessError:
                        continue
                    chosen = repo
                    break
                if chosen is None:
                    self.gaps.append(leaf + ": source revision unavailable " + rev)
                    continue
                self.remember_boundaries(chosen)
                key = "sources/" + rev + ".bundle"
                pack = self.stage / "pinned.bundle"
                with pack.open("wb") as stream:
                    stream.write(("# v2 git bundle\n" + rev + " refs/heads/pinned\n\n").encode())
                    stream.flush()
                    git(chosen, "pack-objects", "--stdout", "--revs", input=(rev + "\n").encode(), stdout=stream)
                self.put(key, pack)
                trees[rev] = tree
            check(not record.get("research_tree") or trees[rev] == record["research_tree"], "Pinned source tree mismatch")
        repo = work / "fledge-source-history"
        if (repo / ".git").exists() or (repo / "HEAD").is_file():
            branch = "refs/heads/research/msi-owner"
            pack = self.stage / "service.bundle"
            git(repo, "bundle", "create", pack, branch)
            self.remember_boundaries(repo)
            self.put("sources/msi-owner.bundle", pack)
        else:
            self.gaps.append("Source-history repository not yet available; workspace runtime files captured")
        return trees

    def personal(self):
        if (self.work / RUNS / "personal.json").exists():
            return "runs/personal.json"
        # An export is a dated snapshot. Never substitute it silently for a live individual.
        for package in sorted((self.work / RUNS / "exports").glob("fledge-lab-*.zip"), reverse=True):
            try:
                with ZipFile(package) as z:
                    entry = z.getinfo("personal.json")
                    check(entry.file_size <= 1 << 20, "Personal checkpoint exceeds limit")
                    data = z.read(entry)
                    manifest = json.loads(z.read("manifest.json"))
                    check(manifest["files"]["personal.json"] == {"size": len(data), "sha256": sha(data)},
                          "Personal export digest differs")
            except PermissionError:
                continue
            src = self.stage / "personal.json"
            src.write_bytes(data)
            self.put("personal-export/personal.json", src)
            self.gaps.append("No current runs/personal.json; restored personal checkpoint is the dated export")
            return package.name
        self.gaps.append("No saved runs/personal.json and no readable personal export; individual recovery unverified")
        return None


def records(directory):
    active = read(directory / "active_research.json")
    result = []
    for child in sorted((directory / "continuations").iterdir()):
        if not child.is_dir():
            continue
        if active["active_path"] == "continuations/" + child.name:
            record = active
        else:
            p = child / "review-at-completion.json"
            if not p.exists():
                p = child / "preregistration.json"
            if not p.exists():
                continue
            record = read(p)
        result.append((child.name, record))
    return result


def study_inventory(directory, files, trees):
    inventory = {}
    for name, r in records(directory):
        leaf = directory / "continuations" / name
        rev, module = r.get("research_revision"), r.get("module_sha256")
        modules = [k for k, v in files.items() if k.startswith("evaluators/") and v["sha256"] == module]
        saved = sorted(p.name for p in leaf.glob("generation-*.json"))
        inventory[name] = dict(protocol=r.get("protocol"), revision=rev, verified_tree=trees.get(rev),
                               exact_evaluator=next(iter(modules), None), module_sha256=module,
                               historical_module_pin_recorded=bool(module), saved_generations=saved,
                               source_path=r.get("source_path"), source_round=r.get("source_round"),
                               pinned_banks=[k for k, v in r.get("reports", {}).items() if isinstance(v, str)],
                               population_database=(leaf / "campaign.sqlite3").is_file(),
                               review_sha256=digest(leaf / "review.json") if (leaf / "review.json").is_file() else None)
    return inventory


def audit(directory):
    active = read(directory / "active_research.json")
    source = safe(directory, active["source_path"]) / "campaign.sqlite3"
    with closing(readonly(source)) as db:
        pop, report = db.execute("select population,report from rounds where generation=?", (active["source_round"],)).fetchone()
    def canonical(data):
        return sha(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    check(canonical(json.loads(zlib.decompress(pop))) == active["parent_population_sha256"] and
          canonical(json.loads(report)) == active["parent_record_sha256"], "Archived source row differs")
    reports = generations = 0
    studies = records(directory)
    for name, reg in studies:
        child = directory / "continuations" / name
        for key, path_key in (("review_sha256", "review_path"), ("previous_review_sha256", "previous_review"),
                              ("proposal_sha256", "proposal_path")):
            if reg.get(key) and reg.get(path_key):
                check(digest(safe(directory, reg[path_key])) == reg[key], "Broken " + key + " for " + name)
        for filename, expected in reg.get("reports", {}).items():
            if isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected):
                check(digest(safe(child, filename)) == expected, "Broken bank " + filename)
                reports += 1
        checkpoints = sorted(child.glob("generation-*.json"))
        if checkpoints:
            anchor = digest(child / "preregistration.json")
            for index, path in enumerate(checkpoints):
                obj = read(path)
                cp = obj["checkpoint"]
                encoded = (json.dumps(cp, indent=2, allow_nan=False) + "\n").encode()
                check(cp["generation"] == index and cp["previous"] == anchor and sha(encoded) == obj["sha256"],
                      "Broken generation chain for " + name)
                anchor = digest(path)
            progress = read(child / "training-progress.json")
            check(progress["checkpoint_sha256"] == anchor and progress["generation"] == len(checkpoints) - 1,
                  "Training progress digest differs")
            for filename in reg.get("reports", {}):
                check(read(safe(child, filename))["bank"]["selection_checkpoint_sha256"] == anchor,
                      "Selection and bank disagree")
            generations += len(checkpoints)
    return dict(studies=len(studies), registered_bank_hashes=reports, generation_checkpoints=generations,
                source_population_sha256=active["parent_population_sha256"])


def restore(store, manifest, dest):
    """Restore to a new directory only. No runnable owner permit or scheduler task is materialized."""
    check(not dest.exists(), "Restore destination already exists")
    dest.mkdir()
    supervisor = dest / CAMP / "supervisor"
    supervisor.mkdir(parents=True)
    (supervisor / "paused").touch()
    for name, spec in manifest["files"].items():
        target = safe(dest, name)
        h = spec["sha256"]
        check(re.fullmatch("[0-9a-f]{64}", h), "Invalid object name")
        source = store / "objects" / h[:2] / h
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        check(target.stat().st_size == spec["size"] and digest(target) == h, "Restored object differs: " + name)
        if target.name == "campaign.sqlite3":
            (target.parent / "paused").touch()
    return dest


def rehearsal(store, manifest, *, retain_as=None):
    retained_path = None
    if retain_as is not None:
        check(re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]{0,60}", retain_as), "Supply one new restore directory name")
        retained_path = safe(store, "paused-restores/" + retain_as)
        check(not retained_path.exists(), "Restore destination already exists")
    with new_tree(store, ".r-") as stage:
        if os.name == "nt" and not str(stage).startswith("\\\\?\\"):
            stage = Path("\\\\?\\" + str(stage))
        dest = restore(store, manifest, stage / "isolated")
        db_count = 0
        for path in (dest / RUNS).rglob("*.sqlite3"):
            with closing(readonly(path)) as db:
                check(db.execute("pragma integrity_check").fetchall() == [("ok",)], "Restored database failed integrity check")
            db_count += 1
        with closing(readonly(dest / RUNS / "personal.json.lineages.sqlite3")) as db:
            generation, tip = db.execute("select completed_generation,tip from branches where id='main'").fetchone()
        lab = json.loads(zlib.decompress(tip))
        check(lab.get("generation") == generation + 1 and isinstance(lab["running"], bool), "Invalid lineage tip")
        campaign = dest / CAMP
        review = read(campaign / "active_research.json")
        check(review["review_sha256"] == manifest["review_sha256"] and (campaign / "supervisor/paused").exists(),
              "Restore anchor or pause differs")
        result = audit(campaign)
        tip_file = stage / "lab-tip.json"
        tip_file.write_bytes(zlib.decompress(tip))
        personal = dest / RUNS / "personal.json"
        if not personal.exists():
            personal = dest / "personal-export/personal.json"
        restore_code = (
            "import json,sys; from pathlib import Path; from wisp.lab import LabController; "
            "from wisp.teach import TeachingSession; "
            "lab=LabController.restore(json.loads(Path(sys.argv[1]).read_bytes())); "
            "lab.running=False; p=Path(sys.argv[2]); "
            "session=TeachingSession(0,p) if p.is_file() else None; "
            "print(json.dumps(dict(generation=lab.generation,paused=not lab.running,"
            "personal_trials=session.wisp.trials if session else None)))"
        )
        probe = subprocess.run([sys.executable, "-B", "-c", restore_code, str(tip_file), str(personal)],
                               cwd=dest / "runtime/fledge", check=True, capture_output=True, timeout=30)
        replay = json.loads(probe.stdout)
        check(replay["generation"] == lab["generation"] and replay["paused"], "Restored laboratory identity differs")
        repo = dest / "recovered-source.git"
        git(stage, "init", "--bare", repo)
        if manifest.get("shallow_boundaries"):
            (repo / "shallow").write_text("\n".join(manifest["shallow_boundaries"]) + "\n", encoding="ascii")
        for rev, tree in manifest["source_trees"].items():
            bundle = dest / "sources" / (rev + ".bundle")
            git(repo, "bundle", "verify", bundle)
            heads = git(repo, "bundle", "unbundle", bundle).decode().splitlines()
            check(heads == [rev + " refs/heads/pinned"], "Restored bundle head differs")
            check(git(repo, "rev-parse", rev + "^{tree}").decode().strip() == tree, "Restored Git tree differs")
            git(repo, "update-ref", "refs/heads/studies/" + rev, rev)
        service_bundle = dest / "sources/msi-owner.bundle"
        service_revision = None
        if service_bundle.exists():
            git(repo, "bundle", "verify", service_bundle)
            head = git(repo, "bundle", "unbundle", service_bundle).decode().split()
            check(len(head) == 2 and head[1] == "refs/heads/research/msi-owner", "Unexpected source-history head")
            service_revision = head[0]
            git(repo, "update-ref", head[1], head[0])
        commits = list(manifest["source_trees"])
        if service_revision:
            commits.append(service_revision)
        git(repo, "fsck", "--full", "--no-dangling", *commits)
        result.update(databases=db_count, lab_generation=lab["generation"], lab_running_in_backup=lab["running"],
                      lab_tip_sha256=sha(tip), personal_trials=replay["personal_trials"],
                      pinned_source_trees=len(manifest["source_trees"]), service_source_revision=service_revision,
                      exact_registered_evaluators=sum(bool(r["exact_evaluator"]) for r in manifest["study_inventory"].values()),
                      paused=True,
                      verified_at=datetime.now(timezone.utc).isoformat())
        if retained_path is not None:
            retained_path.parent.mkdir(exist_ok=True)
            dest.rename(retained_path)
            result["restored_to"] = str(retained_path)
        return result


def capture(work, store, stage, anchor):
    collector = Collector(work, store, stage)
    initial = frozen_index(work)
    code = {p: digest(p) for p in runtime_files(work)}
    captured = {}
    with ExitStack() as stack:
        snapshots = []
        for src in initial:
            name = src.relative_to(work).as_posix()
            if src.is_relative_to(work / OWNER):
                name = "custody/" + name
            if src.suffix != ".sqlite3":
                collector.put(name, src)
                captured[src] = collector.files[name]["sha256"]
                continue
            db = stack.enter_context(closing(readonly(src)))
            version, = db.execute("pragma data_version").fetchone()
            snapshots.append((db, version))
            copy = stage / (uuid4().hex + ".sqlite3")
            with closing(sqlite3.connect(copy)) as target:
                deadline = time.monotonic() + 20
                def progress(status, remaining, total):
                    if time.monotonic() >= deadline:
                        raise BoundaryChanged("Database backup busy; retry at a quiet boundary")
                db.backup(target, pages=512, progress=progress, sleep=.05)
            collector.put(name, copy)
        trees = collector.sources(records(work / CAMP))
        personal = collector.personal()
        final = frozen_index(work)
        # Replacing an identical durable JSON record can change mtime without changing the review.
        changed = set(final) != set(initial) or any(
            final[p] != initial[p] and (p not in captured or digest(p) != captured[p]) for p in initial)
        if (changed or {p: digest(p) for p in runtime_files(work)} != code
                or boundary(work) != anchor or
                any(db.execute("pragma data_version").fetchone()[0] != v for db, v in snapshots)):
            raise BoundaryChanged("Campaign or lineage changed during snapshot")
    return dict(version=1, review_sha256=anchor, files=collector.files, source_trees=trees, gaps=collector.gaps,
                study_inventory=study_inventory(work / CAMP, collector.files, trees),
                shallow_boundaries=sorted(collector.shallow),
                personal_source=personal, captured_at=datetime.now(timezone.utc).isoformat())


def ensure_milestone(work=WORK, *, refresh=False):
    work = Path(work).resolve()
    store = work / BACKUPS
    store.mkdir(exist_ok=True)
    with (store / "backup.lock").open("a+b") as lock:
        if not try_lock(lock):
            raise BoundaryChanged("Another milestone backup is running")
        started = time.monotonic()
        anchor = boundary(work)
        latest = store / "latest.json"
        account = subprocess.check_output(["whoami"], text=True).strip().lower()
        prior = read(latest) if latest.exists() else {}
        if not refresh and prior.get("review_sha256") == anchor and prior.get("account") == account:
            path = safe(store, prior["manifest"])
            check(digest(path) == prior["manifest_sha256"], "Stored milestone manifest differs")
            if time.time() - datetime.fromisoformat(prior["restore"]["verified_at"]).timestamp() < DAY:
                return prior
            manifest = read(path)
        else:
            with new_tree(store, ".capture-") as stage:
                manifest = capture(work, store, stage, anchor)
            path = store / anchor / (uuid4().hex + ".json")
        verified = rehearsal(store, manifest)
        if not path.exists():
            save(path, manifest)
        receipt = dict(account=account, review_sha256=anchor, manifest=path.relative_to(store).as_posix(),
                       manifest_sha256=digest(path), files=len(manifest["files"]), gaps=manifest["gaps"],
                       personal_source=manifest["personal_source"], restore=verified,
                       elapsed_seconds=round(time.monotonic() - started, 2))
        save(path.with_suffix(".receipt.json"), receipt)
        save(latest, receipt)
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--refresh", action="store_true", help="Capture newly available source or personal checkpoints")
    modes.add_argument("--restore", metavar="NAME", help="Verify and retain the latest paused restore under fledge-milestones/paused-restores")
    args = parser.parse_args()
    if args.restore:
        store = WORK / BACKUPS
        receipt = read(store / "latest.json")
        manifest = safe(store, receipt["manifest"])
        check(digest(manifest) == receipt["manifest_sha256"], "Milestone manifest differs")
        result = rehearsal(store, read(manifest), retain_as=args.restore)
    else:
        result = ensure_milestone(refresh=args.refresh)
    print(json.dumps(result, indent=2))

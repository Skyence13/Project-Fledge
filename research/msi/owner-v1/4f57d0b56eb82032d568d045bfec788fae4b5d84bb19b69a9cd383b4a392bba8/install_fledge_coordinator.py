"""Register the MSI coordinator at Morgan's Windows sign-in and start the handover."""

import csv
from datetime import datetime, timezone
import json
import locale
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from xml.etree import ElementTree as ET

TASK = "Project Fledge completion coordinator"
ACCOUNT = r"msi\morga"
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
WORK = Path(__file__).resolve().parent
SETTINGS = {
    "MultipleInstancesPolicy": "IgnoreNew",
    "DisallowStartIfOnBatteries": "false",
    "StopIfGoingOnBatteries": "false",
    "StartWhenAvailable": "true",
    "AllowStartOnDemand": "true",
    "Enabled": "true",
    "ExecutionTimeLimit": "PT0S",
}


def task_xml(work, pythonw, identity):
    root = ET.Element("Task", {"version": "1.2", "xmlns": NS})
    info = ET.SubElement(root, "RegistrationInfo")
    ET.SubElement(info, "Description").text = "Review completed Fledge studies and dispatch one guarded successor on MSI."
    trigger = ET.SubElement(ET.SubElement(root, "Triggers"), "LogonTrigger")
    ET.SubElement(trigger, "Enabled").text = "true"
    ET.SubElement(trigger, "UserId").text = identity
    principal = ET.SubElement(ET.SubElement(root, "Principals"), "Principal", id="Morgan")
    ET.SubElement(principal, "UserId").text = identity
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(root, "Settings")
    for field, value in SETTINGS.items():
        ET.SubElement(settings, field).text = value
    restart = ET.SubElement(settings, "RestartOnFailure")
    ET.SubElement(restart, "Interval").text = "PT1M"
    ET.SubElement(restart, "Count").text = "999"
    action = ET.SubElement(ET.SubElement(root, "Actions", Context="Morgan"), "Exec")
    ET.SubElement(action, "Command").text = str(pythonw)
    ET.SubElement(action, "Arguments").text = f'-B "{work / "fledge_coordinator.py"}" --service'
    ET.SubElement(action, "WorkingDirectory").text = str(work)
    return ET.tostring(root, encoding="utf-16", xml_declaration=True)


def verified_task(raw, expected, identity, sid):
    # Scheduler exports may normalize a user to a SID and omit default settings.
    def parse(data):
        if not data.startswith((b"\xff\xfe", b"\xfe\xff")) and b"\x00" not in data[:24]:
            # Redirected schtasks output may use a console encoding with a UTF-16 declaration.
            try:
                data = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                data = data.decode(locale.getpreferredencoding(False))
        tree = ET.fromstring(data)
        if tree.tag != "{%s}Task" % NS:
            raise ValueError("Invalid Task Scheduler definition")
        for element in tree.iter():
            element.tag = element.tag.removeprefix("{%s}" % NS)
        return tree

    actual, target = parse(raw), parse(expected)
    for container, child in (("Actions", "Exec"), ("Triggers", "LogonTrigger"), ("Principals", "Principal")):
        group = actual.find(container)
        if group is None or [node.tag for node in group] != [child]:
            raise ValueError(f"Existing task has different {container}")
    for key in ("Principals/Principal/UserId", "Triggers/LogonTrigger/UserId"):
        if actual.findtext(key, "").lower() not in {identity.lower(), sid.lower()}:
            raise ValueError("Existing task belongs to another user")
    defaults = {"Principals/Principal/RunLevel": "LeastPrivilege", "Triggers/LogonTrigger/Enabled": "true",
                "Settings/Enabled": "true", "Settings/AllowStartOnDemand": "true",
                "Settings/MultipleInstancesPolicy": "IgnoreNew"}
    for key in ("Actions/Exec/Command", "Actions/Exec/Arguments", "Actions/Exec/WorkingDirectory",
                "Principals/Principal/LogonType", "Principals/Principal/RunLevel", "Triggers/LogonTrigger/Enabled",
                *("Settings/" + name for name in SETTINGS), "Settings/RestartOnFailure/Interval",
                "Settings/RestartOnFailure/Count"):
        if actual.findtext(key, defaults.get(key)) != target.findtext(key):
            raise ValueError(f"Registered task differs at {key}. Inspect the task before retrying.")


def release_legacy_review_owner(work):
    """Let the old idle loop leave its lock before Windows starts the upgraded task."""
    from fledge_completion_watch import try_lock
    state = work/"fledge-coordinator-state"
    grant = state/"standing-authority.json"
    session = state/"service-session.json"
    if not grant.is_file() or not session.is_file():
        return
    info=json.loads(session.read_bytes())
    if info.get("standing_workflow") or info.get("account") != ACCOUNT:
        return
    anchor=json.loads(grant.read_bytes())["anchor"]
    cursor=work/"fledge/runs/personal.json.campaign/active_research.json"
    def idle():
        active=json.loads(cursor.read_bytes())
        journal=json.loads((state/"owner.json").read_bytes())
        return active.get("review_sha256")==anchor and active.get("stop_after_review") is True and journal=={"parent":anchor,"child":None}
    if not idle():
        raise ValueError("The legacy coordinator moved; review its active child before upgrading.")
    stop=state/"stopped.json"
    marker={"maintenance":"idle standing-owner upgrade","review_sha256":anchor,"legacy_pid":info["pid"]}
    if stop.exists() and json.loads(stop.read_bytes())!=marker:
        raise ValueError("The coordinator has an existing stop. Review it before upgrading.")
    if not stop.exists():
        with stop.open("x",encoding="utf-8") as f:
            json.dump(marker,f)
    with (state/"service.lock").open("r+b") as lock:
        deadline=time.monotonic()+30
        while not try_lock(lock):
            if time.monotonic()>deadline:
                raise ValueError("Legacy owner has not yet released its lock. Run the installer again after it exits.")
            time.sleep(.25)
        if not idle() or json.loads(stop.read_bytes())!=marker:
            raise ValueError("Ownership or the recorded stop changed during the upgrade.")
        stop.unlink()
    return True


def install(work, pythonw, identity, sid, hostname, scheduler):
    if hostname.lower() != "msi" or identity.lower() != ACCOUNT:
        raise ValueError("Open Install-FledgeCoordinator.cmd from Morgan's signed-in MSI desktop.")
    if not (work / "fledge_coordinator.py").is_file() or not pythonw.is_file():
        raise ValueError("The coordinator or matching windowless Python interpreter is missing.")
    xml = task_xml(work, pythonw, identity)
    query = ["/Query", "/TN", TASK, "/XML"]
    saved = scheduler(query)
    if saved.returncode:
        with tempfile.TemporaryDirectory(prefix="fledge-task-") as directory:
            path = Path(directory) / "task.xml"
            path.write_bytes(xml)
            # Omit /F: never overwrite a task if another installer created it concurrently.
            create = scheduler(["/Create", "/TN", TASK, "/XML", str(path)])
        saved = scheduler(query)
        if saved.returncode:
            create.check_returncode()
            saved.check_returncode()
    verified_task(saved.stdout, xml, identity, sid)
    upgrade = release_legacy_review_owner(work)
    scheduler(["/Run", "/TN", TASK]).check_returncode()
    state = work / "fledge-coordinator-state"
    state.mkdir(exist_ok=True)
    record = dict(account=identity.lower(), task=TASK, recorded_at=datetime.now(timezone.utc).isoformat())
    with tempfile.NamedTemporaryFile(dir=state, delete=False, mode="w", encoding="utf-8") as out:
        json.dump(record, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, state / "service-account.json")
    if upgrade:
        deadline = time.monotonic()+35
        while not json.loads((state/"service-session.json").read_bytes()).get("standing_workflow"):
            if time.monotonic() >= deadline:
                raise ValueError("Windows accepted the request but the upgraded service has not checked in.")
            time.sleep(2)
            scheduler(["/Run","/TN",TASK]).check_returncode()


def main():
    if os.name != "nt":
        raise ValueError("Run the installer on MSI.")
    system = Path(os.environ["SystemRoot"]) / "System32"
    user = subprocess.check_output([system / "whoami.exe", "/USER", "/FO", "CSV", "/NH"],
                                   text=True, encoding=locale.getpreferredencoding(False))
    identity, sid = next(csv.reader([user.strip()]))
    def scheduler(args):
        return subprocess.run([system / "schtasks.exe", *args], capture_output=True, stdin=subprocess.DEVNULL)

    install(WORK, Path(sys.executable).resolve().with_name("pythonw.exe"), identity, sid, socket.gethostname(), scheduler)
    print(f"Verified and started {TASK} for {identity.lower()}. It will start at this user's next sign-in.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, ET.ParseError, subprocess.CalledProcessError) as exc:
        message = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else None
        sys.exit("Installation stopped. " + (message.decode(errors="replace").strip() if message else str(exc)))

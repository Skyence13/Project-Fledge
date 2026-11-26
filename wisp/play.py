"""Launch the Godot teaching room and its local Wisp together."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import threading

from .teach import build_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", default=os.environ.get("GODOT_BIN"),
                        help="Godot executable path (or set GODOT_BIN)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--save", type=Path, default=Path("runs/personal.json"))
    parser.add_argument("--resume", type=Path, help="Start from a saved individual")
    args = parser.parse_args()
    engine = shutil.which(args.godot) if args.godot else shutil.which("godot") or shutil.which("godot4")
    if not engine:
        parser.error("Install Godot 4.6 and supply its executable with --godot or GODOT_BIN.")

    project = Path(__file__).resolve().parents[1] / "sim"
    try:
        server = build_server(port=8765, seed=args.seed, save_path=args.save, resume_path=args.resume)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Unable to start the Wisp teaching service: {exc}\n")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print("Wisp teaching room is ready. Use Save in the room to keep this lifetime.", flush=True)
    try:
        return subprocess.run([engine, "--path", str(project)], check=False).returncode
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())

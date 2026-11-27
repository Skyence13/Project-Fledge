import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from wisp import play


class LauncherTests(unittest.TestCase):
    def test_bound_port_is_forwarded_and_released(self):
        ports = []

        def connect_as_godot(command, *, env, check):
            self.assertTrue((Path(command[-1]) / "project.godot").is_file())
            port = int(env["FLEDGE_SERVICE_PORT"])
            ports.append(port)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                connection.request("GET", "/state")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["trials"], 0)
            finally:
                connection.close()
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "separate.json"
            with patch.dict(os.environ, {"FLEDGE_SERVICE_PORT": "bogus"}), \
                    patch.object(play.subprocess, "run", side_effect=connect_as_godot), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(play.main(["--godot", sys.executable, "--port", "0",
                                            "--save", str(checkpoint)]), 0)
                self.assertEqual(os.environ["FLEDGE_SERVICE_PORT"], "bogus")
            self.assertFalse(checkpoint.exists())

        self.assertEqual(len(ports), 1)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", ports[0]), timeout=.25)

    def test_rejects_invalid_port_before_starting_server(self):
        with patch.object(play, "build_server") as build_server, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            play.main(["--port", "65536"])
        self.assertEqual(stopped.exception.code, 2)
        build_server.assert_not_called()

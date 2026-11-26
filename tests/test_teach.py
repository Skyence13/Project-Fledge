import http.client
import json
from pathlib import Path
import random
import tempfile
import threading
import unittest
from unittest.mock import patch

from wisp.core import Wisp, random_genome
from wisp.teach import TeachingHandler, build_server


class TeachingHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / "saved" / "personal.json"
        reporting = patch.object(TeachingHandler, "log_message")
        reporting.start()
        self.addCleanup(reporting.stop)
        self.server = None
        self.addCleanup(self.stop_server)
        self.start_server()

    def start_server(self, resume=None):
        self.server = build_server(port=0, seed=7, save_path=self.path, resume_path=resume)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(2)
            self.server.server_close()
            self.server = None

    def request(self, path, body=None, expected=200):
        host, port = self.server.server_address
        self.assertEqual(host, "127.0.0.1")
        connection = http.client.HTTPConnection(host, port, timeout=3)
        try:
            if body is None:
                connection.request("GET", path)
            else:
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                connection.request("POST", path, payload, {"Content-Type": "application/json"})
            result = connection.getresponse()
            data = json.loads(result.read())
            self.assertEqual(result.status, expected, data)
            if expected == 200:
                self.assertEqual(set(data), {"cue", "action", "pending", "trials", "saved"})
            else:
                self.assertIsInstance(data["error"], str)
            return data
        finally:
            connection.close()

    def test_teaching_order_and_load(self):
        self.assertEqual(self.request("/state"), {
            "cue": None, "action": None, "pending": False, "trials": 0, "saved": False,
        })
        self.request("/feedback", {"value": 1}, 409)
        self.request("/load", {}, 404)
        for cue in (True, -1, 2, "1", None):
            self.request("/choice", {"cue": cue}, 400)
        self.request("/choice", b"{", 400)
        self.request("/choice", ["wrong shape"], 400)
        pending = self.request("/choice", {"cue": 1})
        self.assertIn(pending["action"], (0, 1))
        self.assertTrue(pending["pending"])
        self.request("/choice", {"cue": 0}, 409)
        for invalid in (True, 0, 2, "1"):
            self.request("/feedback", {"value": invalid}, 400)
        self.assertEqual(self.request("/state"), pending)
        self.assertTrue(self.request("/save", {})["saved"])
        saved = self.request("/state")
        result = self.request("/feedback", {"value": -1})
        self.assertFalse(result["pending"])
        self.assertEqual(result["trials"], 1)
        self.request("/feedback", {"value": 1}, 409)
        self.assertEqual(self.request("/load", {}), saved)
        self.assertEqual(self.request("/feedback", {"value": -1}), result)

    def test_restart_with_pending_feedback_and_exact_continuation(self):
        self.request("/choice", {"cue": 0})
        self.request("/feedback", {"value": 1})
        self.request("/choice", {"cue": 1})
        checkpoint_state = self.request("/save", {})
        future = []
        for cue in (0, 1, 1, 0, 0, 1):
            self.request("/feedback", {"value": 1})
            future.append(self.request("/choice", {"cue": cue}))
        expected_brain = self.server.session.wisp.checkpoint()
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request("/state"), checkpoint_state)
        for state in future:
            self.request("/feedback", {"value": 1})
            self.assertEqual(self.request("/choice", {"cue": state["cue"]}), state)
        self.assertEqual(self.server.session.wisp.checkpoint(), expected_brain)

    def test_resume_raw_individual_checkpoint(self):
        champion_path = self.folder / "champion.json"
        original = Wisp(random_genome(random.Random(21)), seed=21)
        for cue in (0, 1, 0):
            original.choose(cue)
            original.feedback(1)
        champion_path.write_text(json.dumps(original.checkpoint()), encoding="utf-8")
        self.stop_server()
        self.start_server(resume=champion_path)
        state = self.request("/state")
        self.assertFalse(state["saved"])
        self.assertFalse(state["pending"])
        self.assertEqual(state["trials"], 3)
        self.assertEqual(self.request("/choice", {"cue": 1})["action"], original.choose(1))
        self.request("/feedback", {"value": -1})
        original.feedback(-1)
        self.request("/save", {})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), original.checkpoint())
        self.assertNotEqual(champion_path.read_bytes(), self.path.read_bytes())

    def test_failed_save_preserves_file_and_failed_load_preserves_session(self):
        self.request("/save", {})
        saved = self.path.read_bytes()
        pending = self.request("/choice", {"cue": 1})
        with patch("wisp.teach.os.replace", side_effect=OSError("read only")):
            self.request("/save", {}, 500)
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        for corrupt in ("truncated{", "{}", '{"pending": false}'):
            self.path.write_text(corrupt, encoding="utf-8")
            self.request("/load", {}, 422)
            self.assertEqual(self.request("/state"), pending)


if __name__ == "__main__":
    unittest.main()

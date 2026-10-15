import unittest
import sys
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication

from ui.progress_dialog import AnalysisWorker
from core.runtime_dependencies import (
    missing_research_dependencies, repair_research_dependencies,
)


class _FatalLikeError(BaseException):
    """Simulates exceptions that were previously able to escape QThread.run."""


class BackgroundStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_worker_contains_all_task_failures(self):
        errors = []
        done = []

        def fail(_progress):
            raise _FatalLikeError("simulated worker failure")

        worker = AnalysisWorker([("失败步骤", fail)])
        worker.step_error.connect(
            lambda _index, name, details: errors.append((name, details)))
        worker.all_done.connect(lambda: done.append(True))
        worker.run()

        self.assertEqual(len(errors), 1)
        self.assertIn("simulated worker failure", errors[0][1])
        self.assertEqual(done, [True])

    def test_research_dependency_probe_reports_missing_module(self):
        with patch("core.runtime_dependencies.importlib.import_module",
                   side_effect=ModuleNotFoundError("No module named 'shapely'")):
            self.assertEqual(missing_research_dependencies(), ("shapely",))

    def test_research_dependency_repair_uses_fixed_package_allowlist(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch("core.runtime_dependencies.missing_research_dependencies",
                   side_effect=[("shapely",), ()]), \
             patch("core.runtime_dependencies.subprocess.run", return_value=completed) as run:
            result = repair_research_dependencies()
        self.assertTrue(result.ok)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "pip", "install"])
        self.assertEqual(command[4:], ["Shapely>=2.0.0"])

    def test_cancelled_worker_does_not_publish_partial_result(self):
        published = []
        done = []
        worker = None

        def cancel_during_step(progress):
            progress(1, 2)
            worker.cancel()
            return ("partial", None, None)

        worker = AnalysisWorker([("取消步骤", cancel_during_step)])
        worker.step_done.connect(lambda *_args: published.append(True))
        worker.all_done.connect(lambda: done.append(True))
        worker.run()

        self.assertEqual(published, [])
        self.assertEqual(done, [True])


if __name__ == "__main__":
    unittest.main()

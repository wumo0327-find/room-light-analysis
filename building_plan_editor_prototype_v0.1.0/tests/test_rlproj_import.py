"""Regression tests for legacy and v3 RoomLight project import."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from rlproj_import import load_rlproj


class RlprojImportTests(unittest.TestCase):
    def _write(self, data: dict) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "sample.rlproj"
        path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_legacy_rectangle_import(self) -> None:
        path = self._write({
            "room": {
                "width": 4000,
                "length": 6000,
                "height": 3300,
                "thermal": {"wall_thickness_mm": 240},
                "windows": [{
                    "id": 1,
                    "wall": "south",
                    "x": 500,
                    "y": 600,
                    "width": 1800,
                    "height": 2100,
                }],
            },
        })
        result = load_rlproj(path)
        self.assertEqual(len(result.document.walls), 4)
        self.assertEqual(len(result.document.windows), 1)
        self.assertEqual(len(result.document.recognised_rooms()), 1)

    def test_v3_shared_reversed_wall_is_deduplicated(self) -> None:
        shared_forward = {
            "id": "shared_a",
            "start": [3000, 0],
            "end": [3000, 3000],
            "thickness_mm": 200,
            "openings": [],
        }
        shared_reverse = {
            "id": "shared_b",
            "start": [3000, 3000],
            "end": [3000, 0],
            "thickness_mm": 200,
            "openings": [],
        }
        data = {
            "project_kind": "building",
            "building": {
                "name": "共享墙测试",
                "storeys": [{
                    "name": "首层",
                    "default_height_mm": 3000,
                    "spaces": [
                        {
                            "height_mm": 3000,
                            "boundary_loops": [{
                                "segments": [
                                    {"start": [0, 0], "end": [3000, 0]},
                                    shared_forward,
                                    {"start": [3000, 3000], "end": [0, 3000]},
                                    {"start": [0, 3000], "end": [0, 0]},
                                ],
                            }],
                            "exterior_barriers": [],
                        },
                        {
                            "height_mm": 3000,
                            "boundary_loops": [{
                                "segments": [
                                    {"start": [3000, 0], "end": [6000, 0]},
                                    {"start": [6000, 0], "end": [6000, 3000]},
                                    {"start": [6000, 3000], "end": [3000, 3000]},
                                    shared_reverse,
                                ],
                            }],
                            "exterior_barriers": [],
                        },
                    ],
                }],
            },
        }
        result = load_rlproj(self._write(data))
        self.assertEqual(len(result.document.walls), 7)
        self.assertEqual(len(result.document.recognised_rooms()), 2)


if __name__ == "__main__":
    unittest.main()

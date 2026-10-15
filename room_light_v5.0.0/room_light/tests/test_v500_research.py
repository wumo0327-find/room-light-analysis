from pathlib import Path
import tempfile

import pandas as pd

from core.legacy_adapter import building_from_room
from core.models import RoomModel, Window
from core.research_energy import build_energyplus_model
from core.research_radiance import run_radiance_daylight
from core.research_scene import space_scene


def _building():
    room = RoomModel(width=6000,length=6000,height=3600,windows=[
        Window(wall='south',x=1500,width=3000,y=600,height=2400,id=1)
    ])
    return building_from_room(room)


def test_research_scene_is_finite_and_fingerprinted():
    scene = space_scene(_building().get_space('legacy_space'))
    assert any(surface.kind == 'glass' for surface in scene.surfaces)
    assert any(surface.kind == 'floor' for surface in scene.surfaces)
    assert len(scene.fingerprint()) == 64


def test_energyplus_model_and_exact_teaching_calendar_are_exported():
    with tempfile.TemporaryDirectory() as directory:
        model = build_energyplus_model(_building(),['legacy_space'],directory)
        assert model.exists()
        text = model.read_text(encoding='utf-8')
        assert 'Version,' in text and '25.2;' in text
        assert 'FenestrationSurface:Detailed' in text
        schedule = pd.read_csv(Path(directory)/'teaching_schedule.csv')
        assert len(schedule) == 365*24*6
        assert schedule['lighting_fraction'].between(0,1).all()


def test_bundled_radiance_smoke_run():
    root = Path(__file__).resolve().parents[2]
    if not (root/'engines'/'radiance'/'bin'/'rtrace.exe').exists():
        return
    with tempfile.TemporaryDirectory() as directory:
        result = run_radiance_daylight(
            _building().get_space('legacy_space'),directory,
            grid_mm=2000,ambient_bounces=2,ambient_divisions=64,
        )
        assert result.average_lux > 0
        assert result.minimum_lux >= 0
        assert 0 <= result.uniformity_u0 <= 1
        assert (Path(directory)/'计算溯源.json').exists()
        assert (Path(directory)/'完整网格结果.npz').exists()

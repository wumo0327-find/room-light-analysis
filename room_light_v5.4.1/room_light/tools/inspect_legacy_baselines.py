"""Read-only diagnosis: run inside the requested historical version directory."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from io_utils.project_io import load_building_project
from io_utils.model_audit import audit_building_geometry
from core.complex_daylight import compute_complex_daylight
from core.complex_thermal import compute_complex_thermal
from core.operation import calculate_operation_metrics
from core.space_geometry import space_floor_area_mm2
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--project", required=True)
parser.add_argument("--space", default="bp_space_2")
parser.add_argument("--out", required=True)
args = parser.parse_args()
if args.project.endswith(".bplan.json"):
    from bp_editor.model import DraftDocument
    from io_utils.bp_bridge import building_from_document
    from io_utils.weather_data import default_dataset
    from core.complex_models import BuildingModel
    document = DraftDocument.from_dict(json.loads(Path(args.project).read_text(encoding="utf-8-sig")))
    b, _, _ = building_from_document(document, BuildingModel(), project_name=Path(args.project).stem)
    weather, error = default_dataset(), None
else:
    b, weather, active, error = load_building_project(args.project)
if error:
    raise ValueError(error)
s = b.get_space(args.space)
if s is None:
    raise ValueError("Actual room ID missing")
d = compute_complex_daylight(s, E_out=weather.annual_avg, grid_mm=250, ndiv=20)
t = compute_complex_thermal(s, weather.monthly_ghi, weather.monthly_temp,
    latitude_deg=b.location.latitude, north_angle_deg=b.north_angle_deg)
values = d.DF[d.valid_mask]
record = dict(project=str(Path(args.project).resolve()), version_directory=str(Path.cwd()),
    fingerprint=audit_building_geometry(b).fingerprint, space_id=s.id,
    area_m2=space_floor_area_mm2(s)/1e6, sensor_count=int(values.size),
    zero_count=int(np.sum(values == 0)), Ra=d.Ra, Cd=d.daylight_score, U0=d.U0,
    DFavg=d.DF_avg, Eavg=d.E_avg, DFmin=d.DF_min,
    Tdis=t.overheat_degree_months+t.underheat_degree_months,
    operation=calculate_operation_metrics(daylight_score=d.daylight_score,
        thermal_result=t, floor_area_m2=space_floor_area_mm2(s)/1e6))
Path(args.out).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(record, ensure_ascii=True))

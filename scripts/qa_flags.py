"""Opt-in engine/generation/checkpoint/streaming validation in local Docker QA."""
import argparse
from pathlib import Path

from qa_live_system import Probe, synthetic
from sqlalchemy import text

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path("artifacts/full-system-20261009/flags-enabled"))
output = parser.parse_args().output
probe = Probe(output)
probe.check("login", probe.login)


def validate(count):
    result = probe.run(probe.upload(content=synthetic(count)), count)
    with probe.engine.connect() as db:
        selection = db.execute(text("SELECT engine_version,configuration FROM mdm_resolution_engine_selections WHERE run_id=:run"),
                               {"run": result["run_id"]}).mappings().one()
        assert selection["engine_version"] == "p119_person_v2"
        assert all(selection["configuration"].values()) and len(selection["configuration"]) == 9
        generation = db.execute(text("SELECT g.state,g.record_count,g.metrics,g.artifact_backend FROM mdm_resolution_publications p "
                                     "JOIN mdm_resolution_generations g ON g.id=p.active_generation_id WHERE p.run_id=:run"),
                                {"run": result["run_id"]}).mappings().one()
        assert generation["state"] == "PROMOTED" and generation["record_count"] == count
        checkpoints = db.execute(text("SELECT count(*) FROM mdm_checkpoints WHERE run_id=:run"),
                                 {"run": result["run_id"]}).scalar_one()
        assert checkpoints > 0
    result.update(engine_version=selection["engine_version"], enabled_features=sorted(selection["configuration"]),
                  generation_state=generation["state"], artifact_backend=generation["artifact_backend"], checkpoints=checkpoints)
    return result


try:
    for count in (100, 1000):
        result = probe.check(f"v2_streaming_resume_{count}", lambda count=count: validate(count))
        if result["status"] == "PASS":
            probe.check(f"v2_export_{count}", lambda result=result: probe.workflow(result["run_id"]))
finally:
    probe.client.close()
    probe.engine.dispose()
    probe.save()
raise SystemExit(int(any(case["status"] != "PASS" for case in probe.results)))

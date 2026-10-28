import json

import pytest

from seat_dashboard.jobs.prune_snapshots import prune_runtime


def test_retention_is_explicit_bounded_and_confined(tmp_path):
    snapshots, staging, manifests = (tmp_path / name for name in ("snapshots", "staging", "manifests"))
    for path in (snapshots, staging, manifests): path.mkdir()
    for batch in range(1, 5):
        (snapshots / f"seat_dashboard_b{batch:08d}.duckdb").write_bytes(b"snapshot")
        (staging / f"seat_dashboard_b{batch:08d}.duckdb.tmp").write_bytes(b"staging")
    (snapshots / "research.duckdb").write_bytes(b"keep research")
    manifest = {"database_path": "snapshots/seat_dashboard_b00000004.duckdb", "complete": True}
    (manifests / "current.json").write_text(json.dumps(manifest))
    plan = prune_runtime(tmp_path, min_age_hours=0)
    assert len(plan["delete"]) == 5 and not plan["applied"]
    assert len(list(snapshots.iterdir())) == 5
    assert not prune_runtime(tmp_path)["delete"]  # Recent files protected by default.
    applied = prune_runtime(tmp_path, apply=True, min_age_hours=0)
    assert applied["delete"] == plan["delete"]
    assert len(list(staging.iterdir())) == 1
    assert {path.name for path in snapshots.iterdir()} == {"research.duckdb", "seat_dashboard_b00000003.duckdb", "seat_dashboard_b00000004.duckdb"}
    outside = tmp_path.parent / "unrelated.duckdb"
    outside.write_bytes(b"not a runtime snapshot")
    manifest["database_path"] = str(outside)
    (manifests / "current.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError): prune_runtime(tmp_path, apply=True)
    assert outside.exists()

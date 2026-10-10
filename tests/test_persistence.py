"""Data must survive an application restart (new app instance, same SQLite file)."""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import (
    get_measurements,
    kml_document,
    line_placemark,
    point_placemark,
    polygon_placemark,
    rect_ring,
    upload_ok,
)


def test_results_persist_across_restart(settings):
    content = kml_document(
        polygon_placemark("lot", rect_ring(80.2, 13.0, 0.01, 0.01)),
        line_placemark("road", [(80.2, 13.0), (80.2, 13.05)]),
        point_placemark("pin", 80.2, 13.0),
    )

    with TestClient(create_app(settings)) as first_run:
        uploaded = upload_ok(first_run, "persist.kml", content)
        info_before = first_run.get(f"/api/files/{uploaded['id']}/").json()
        measurements_before = get_measurements(first_run, uploaded["id"])

    assert settings.database_path.exists()

    with TestClient(create_app(settings)) as second_run:  # "restart": brand-new app, same database file
        info_after = second_run.get(f"/api/files/{uploaded['id']}/")
        assert info_after.status_code == 200
        assert info_after.json() == info_before
        assert get_measurements(second_run, uploaded["id"]) == measurements_before
        assert second_run.get("/api/files/unknown-id/").status_code == 404


def test_tests_use_isolated_databases(settings, tmp_path):
    assert str(settings.database_path).startswith(str(tmp_path))

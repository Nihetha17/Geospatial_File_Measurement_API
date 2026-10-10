from datetime import datetime

from tests.helpers import kml_document, point_placemark, polygon_placemark, rect_ring, upload_ok


def test_file_information_is_returned(client):
    content = kml_document(polygon_placemark("lot", rect_ring(80.2, 13.0, 0.01, 0.01)), point_placemark("pin", 80.2, 13.0))
    uploaded = upload_ok(client, "survey.kml", content)

    response = client.get(f"/api/files/{uploaded['id']}/")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == uploaded["id"]
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 2
    assert body["file_type"] == "kml"
    assert "4326" in body["crs"]
    assert body["crs_type"] == "geographic"
    assert body["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}
    datetime.fromisoformat(body["uploaded_at"].replace("Z", "+00:00"))  # parses as a timestamp


def test_unknown_file_id_returns_404(client):
    response = client.get("/api/files/does-not-exist/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_unknown_file_id_measurements_returns_404(client):
    response = client.get("/api/files/does-not-exist/measurements/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"

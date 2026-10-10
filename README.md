# Geospatial File Measurement API

A FastAPI service that accepts **KML** files and **zipped Shapefiles**, extracts every feature (geometry, CRS, attributes), and returns **area in square metres** for polygons and **length in metres** for lines. Measurements are always calculated in a suitable *projected* coordinate system — never directly on longitude/latitude degrees.

Built as an SDE internship assignment. Everything described in this README as "implemented" exists in the code; ideas that are not built are listed separately under [Future scope](#22-future-scope).

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Original problem statement](#2-original-problem-statement)
3. [Key features](#3-key-features)
4. [Technology stack](#4-technology-stack)
5. [Architecture and folder structure](#5-architecture-and-folder-structure)
6. [File-processing workflow](#6-file-processing-workflow)
7. [Measurement-calculation workflow](#7-measurement-calculation-workflow)
8. [CRS transformation and projection-selection strategy](#8-crs-transformation-and-projection-selection-strategy)
9. [Design decisions and alternatives considered](#9-design-decisions-and-alternatives-considered)
10. [Installation prerequisites](#10-installation-prerequisites)
11. [Windows setup instructions](#11-windows-setup-instructions)
12. [Virtual environment creation and activation](#12-virtual-environment-creation-and-activation)
13. [Dependency installation](#13-dependency-installation)
14. [Running the FastAPI application](#14-running-the-fastapi-application)
15. [Swagger UI at `/docs`](#15-swagger-ui-at-docs)
16. [API endpoints and example responses](#16-api-endpoints-and-example-responses)
17. [cURL examples](#17-curl-examples)
18. [Running the automated tests](#18-running-the-automated-tests)
19. [Security considerations](#19-security-considerations)
20. [Assumptions and known limitations](#20-assumptions-and-known-limitations)
21. [Learning outcomes](#21-learning-outcomes)
22. [Future scope](#22-future-scope)
23. [GitHub repository setup and submission](#23-github-repository-setup-and-submission)

---

## 1. Project overview

Surveyors, GIS analysts and mapping tools exchange geospatial data as KML files or Shapefiles. This API lets a client upload such a file once, then retrieve:

* basic file information (feature count, CRS, processing status, upload time), and
* per-feature measurements together with the original geometry and attributes.

Processed results are stored in **SQLite**, so they survive application restarts and never need to be recomputed.

## 2. Original problem statement

> Build a **Geospatial File Measurement API** with FastAPI (Python 3.11 or compatible).
>
> * `POST /api/files/` — accept `.kml` files and `.zip` files containing a Shapefile dataset; respond `201` on success.
> * For every feature identify and preserve: feature ID/index, geometry type, geometry, CRS, properties/attributes. Unsupported geometry types must be handled gracefully.
> * Polygon → area in m². LineString → length in metres. Point → no measurement.
> * Never calculate area/length directly on geographic degrees. For EPSG:4326 pick a suitable projected CRS (prefer local UTM) and transform first. Handle projected CRS with other units (e.g. feet) by converting to metres. Never silently assume a missing CRS. Handle large extents and projection limitations safely. Include source and measurement CRS in responses.
> * `GET /api/files/{id}/` — id, filename, feature count, source CRS, processing status, upload timestamp; `404` for unknown IDs.
> * `GET /api/files/{id}/measurements/` — per-feature measurements, geometry type, ID/index, attributes, CRS details, status and values.
> * `GET /health` — JSON confirming the service is running.
> * Stack: FastAPI, Uvicorn, GeoPandas, Shapely, PyProj, Fiona/Pyogrio, Pydantic, SQLite, python-multipart, Pytest, HTTPX.

## 3. Key features

**Implemented**

* Upload of `.kml` and `.zip` (one Shapefile per ZIP) with HTTP `201`.
* Per-feature results: index, source FID, layer, geometry type, GeoJSON geometry, attributes, measurement status, measurement CRS, area/length with units, warnings.
* Polygon / MultiPolygon area (m²) and LineString / MultiLineString length (m); polygon holes are handled.
* Points / MultiPoints reported as `NOT_REQUIRED`; GeometryCollections and other types reported as `UNSUPPORTED_GEOMETRY`; null and empty geometries reported without failing the request.
* Invalid polygons (e.g. self-intersections) are repaired with `make_valid`, measured, and flagged with a warning.
* Local-UTM measurement for geographic CRS; ellipsoidal (geodesic) fallback for features too large for one UTM zone, near the poles, or crossing the antimeridian.
* Projected CRS in metres, feet, US survey feet etc. are converted to metres / m².
* Web Mercator (EPSG:3857-style) data is re-projected before measuring because Web Mercator badly inflates area away from the equator.
* A missing CRS is reported (`crs: null`, status `CRS_MISSING`) — it is never guessed.
* SQLite persistence across restarts; paginated measurement retrieval (`limit`, `offset`).
* Extensive upload validation and ZIP hardening (see [Security](#19-security-considerations)).
* One consistent JSON error format and a Pytest suite using temporary databases and synthetic data.

## 4. Technology stack

| Purpose | Library |
|---|---|
| Web framework / server | FastAPI, Uvicorn |
| Multipart upload parsing | python-multipart |
| Data models / validation | Pydantic v2 |
| Reading KML & Shapefile (GDAL) | **Pyogrio** (via GeoPandas) |
| Geometry operations | Shapely 2, GeoPandas |
| CRS handling & reprojection, geodesics | PyProj |
| Persistence | SQLite (`sqlite3` from the standard library) |
| Tests | Pytest, HTTPX (via FastAPI `TestClient`) |

**Why Pyogrio instead of Fiona?** Both are allowed. Pyogrio ships GDAL inside its Windows wheels, installs with plain `pip`, and is GeoPandas' default reader — the easiest option for a student on Windows.

## 5. Architecture and folder structure

```
geospatial-file-measurement-api/
├── app/
│   ├── main.py                     # create_app() factory, router + error-handler wiring
│   ├── core/
│   │   ├── config.py               # Settings (limits, DB path) from GEO_* env vars / .env
│   │   ├── exceptions.py           # AppError (code + HTTP status + message)
│   │   └── error_handlers.py       # one JSON error format for every error
│   ├── api/
│   │   ├── dependencies.py         # settings / repository dependencies
│   │   └── routes/
│   │       ├── files.py            # POST /api/files/, GET .../{id}/, GET .../{id}/measurements/
│   │       └── health.py           # GET /health
│   ├── schemas/                    # Pydantic request/response models
│   │   ├── file.py                 # UploadResponse, FileInfoResponse, FileStatus
│   │   ├── measurement.py          # FeatureMeasurement, MeasurementsResponse, MeasurementStatus
│   │   └── error.py                # ErrorResponse
│   ├── services/
│   │   ├── file_validation.py      # filename sanitising, size limits, KML check, safe ZIP extraction
│   │   ├── geospatial_processor.py # reads KML / Shapefile via pyogrio -> plain Python objects
│   │   ├── crs_utils.py            # CRS classification, UTM zone choice, cached transformers
│   │   ├── measurement_service.py  # area / length for one geometry
│   │   ├── file_service.py         # orchestrates upload -> validate -> read -> measure -> store
│   │   └── serialization.py        # NaN/numpy/datetime -> JSON-safe values
│   ├── repositories/
│   │   └── file_repository.py      # SQLite access (files + features tables)
│   └── models/
│       └── domain.py               # internal dataclasses (SourceFeature, ParsedDataset, FileRecord)
├── tests/                          # Pytest suite (see section 18)
├── samples/                        # sample KML + generator for a sample Shapefile ZIP
├── requirements.txt
├── pytest.ini
├── .env.example
├── .gitignore
└── README.md
```

Layering: **routes** (HTTP only) → **file_service** (workflow) → **validation / processor / measurement** (business logic) → **repository** (SQLite). Routes contain no business logic; measurement code never touches HTTP or the database.

## 6. File-processing workflow

`POST /api/files/` runs these steps (see `app/services/file_service.py`):

1. **Early size check** from the `Content-Length` header (cheap rejection of huge uploads).
2. **Filename sanitising** — directory parts and unsafe characters are removed. The name is stored only as text; it is *never* used as a file-system path.
3. **Extension check** — only `.kml` and `.zip` are accepted (`415` otherwise).
4. **Streamed save** into a private temporary directory under a fixed name, aborting the moment the size limit is exceeded (`413`). Empty files are rejected (`400`).
5. **Content validation**
   * KML: must be well-formed XML with a `<kml>` root; binary content and `DOCTYPE`/`ENTITY` declarations are rejected.
   * ZIP: safety checks, then exactly one Shapefile with `.shp`, `.shx` and `.dbf` must be present; those parts are extracted to fixed names (`dataset.shp`, …).
6. **Read** with GDAL (through Pyogrio). KML files are read layer by layer (one layer per KML folder) in order. A feature limit is enforced while reading.
7. **Convert** attributes to JSON-safe values (NaN → `null`, numpy types → Python types, timestamps → ISO strings).
8. **Measure** every feature (section 7) — one bad feature never fails the request.
9. **Persist** the file row and all feature results in one SQLite transaction.
10. **Clean up** — the temporary directory is always deleted.
11. Respond `201` with id, filename, feature count, CRS, status and warnings.

**Processing status** values: `COMPLETED` (everything measured or not applicable, no warnings) and `COMPLETED_WITH_WARNINGS` (missing CRS, skipped/unsupported/invalid features, repaired geometries, empty file…). Because processing is synchronous, uploads that fail validation return an error and are not stored, so there is no `FAILED` file record.

## 7. Measurement-calculation workflow

For each feature, `measure_geometry()` decides in this order:

| Condition | Resulting `measurement_status` |
|---|---|
| No geometry | `NULL_GEOMETRY` |
| Empty geometry | `EMPTY_GEOMETRY` |
| Point / MultiPoint | `NOT_REQUIRED` |
| Not polygonal/linear (e.g. GeometryCollection) | `UNSUPPORTED_GEOMETRY` |
| Polygonal/linear but dataset has no CRS | `CRS_MISSING` |
| CRS type cannot be used (e.g. geocentric) | `MEASUREMENT_FAILED` |
| Polygon invalid and not repairable | `INVALID_GEOMETRY` |
| Transformation/calculation error, out-of-range coordinates | `MEASUREMENT_FAILED` |
| Success | `MEASURED` (with `area` + `area_unit`, or `length` + `length_unit`) |

Units are `square_metres` and `metres`. Z coordinates are dropped before measuring. Values are rounded to 4 decimals. Details of the CRS logic follow.

## 8. CRS transformation and projection-selection strategy

The dataset CRS is classified once (`crs_utils.build_crs_context`):

| Source CRS | What happens | `measurement_crs` |
|---|---|---|
| **Missing** | Polygons/lines are *not* measured (`CRS_MISSING`). The CRS is never guessed. | `null` |
| **Projected** (UTM, State Plane, …) | Measured directly in the source CRS; the CRS's linear unit factor is applied — length × factor, area × factor² (metre → 1, international foot → 0.3048, US survey foot → 0.3048006…). | the source CRS |
| **Web Mercator** (EPSG:3857 & aliases) | Converted back to geographic coordinates, then handled like the geographic case below, with a note. | UTM / geodesic |
| **Geographic** (EPSG:4326, NAD83, …) | Per feature: the UTM zone is chosen from the centre of the feature's bounding box (`EPSG:326xx` north, `EPSG:327xx` south); the geometry is transformed with PyProj and measured there. | `EPSG:326xx/327xx` |
| **Geographic, feature too large** | Wider than one 6° UTM zone, outside latitude −80°…84°, or crossing the antimeridian → measured on the ellipsoid with PyProj's `Geod`, with a warning. | `GEODESIC:<ellipsoid>` |
| **Other** (geocentric, non-degree angular units, …) | `MEASUREMENT_FAILED` with an explanation. | `null` |

Because the UTM zone is picked **per feature**, a file spanning several zones is still measured sensibly. The response's top-level `measurement_crs` is the single CRS used if all measured features share it, `"MULTIPLE"` if it varies, or `null` if nothing was measured.

**Limitations of this strategy**

* **UTM is not equal-area.** Its scale factor ranges from 0.9996 (central meridian) to about 1.001 (zone edge), so planar areas can differ from true ellipsoidal areas by roughly **±0.2 %** and lengths by about **±0.1 %**. This is normal survey-grade-adjacent accuracy for the purpose, but not geodetic accuracy. The geodesic fallback is more accurate but is used only for large features.
* Features using the geodesic path: polygon edges are geodesics between vertices (not parallels). Polygons that enclose a pole are not specially handled.
* Datum shifts between a non-WGS 84 geographic CRS and WGS 84 UTM use PyProj's default operation (metre-level at worst, irrelevant for area/length).
* For a *projected* source CRS the area/length are measured in that CRS as declared. If the coordinates lie far outside that CRS's valid area, results are distorted (the API does not check the CRS area of use).
* KML is defined to be WGS 84 by its specification, so `EPSG:4326` is used for KML (this is a format rule, not a guess; a note appears in warnings only if GDAL reports no CRS).

## 9. Design decisions and alternatives considered

| Decision | Reason | Alternatives considered |
|---|---|---|
| **Synchronous endpoints** (`def`, not `async def`) | GDAL, Shapely and `sqlite3` are blocking; FastAPI runs `def` endpoints in a thread pool so the event loop is not blocked. | Background tasks / a job queue (needs polling and a `PROCESSING` state — overkill for this scope). |
| **Measure at upload, store results** | `GET` endpoints are fast and need no geometry libraries; results are stable across library upgrades. | Store raw files and measure on every request (slower, needs file storage). |
| **Features stored as JSON in SQLite** | Exactly the API shape; trivial to read back and paginate. | Normalised columns / SpatiaLite (more schema work, little benefit here). |
| **`sqlite3` without an ORM** | Two tables; fewer dependencies and less to learn. | SQLAlchemy (useful when schemas grow or databases change). |
| **UTM first, geodesic only for large features** | Matches the brief ("prefer UTM"), projected maths is easy to verify. | Geodesic for everything (more accurate, but the brief asks for projection-based measurement); custom equal-area projection per feature (best area accuracy, harder to explain/verify). |
| **Per-feature UTM zone** | Correct for files that span zones. | One zone per file (simpler, wrong for wide files). |
| **Refuse to guess a missing CRS** | Guessing silently produces wrong numbers. | Defaulting to EPSG:4326 (explicitly forbidden). |
| **Reject (not guess) multiple Shapefiles in a ZIP** | Ambiguity should be explicit. | Process the first one. |
| **Pyogrio over Fiona** | Wheel with bundled GDAL → painless on Windows. | Fiona (also fine). |
| **Fixed temp filenames** | Archive/user names never reach the file system. | Sanitised original names (still riskier). |
| **App factory + injected settings** | Tests build an app with a temporary database. | Global settings (tests would share state). |

## 10. Installation prerequisites

* **Windows 10 or 11** (macOS/Linux work with the same steps, using `source .venv/bin/activate`).
* **Python 3.11 or 3.12, 64-bit** from <https://www.python.org/downloads/windows/>. During installation tick **"Add python.exe to PATH"**. (Python 3.13+ may lack pre-built wheels for some geospatial packages at the pinned versions — use 3.11 or 3.12.)
* **Visual Studio Code** with the *Python* extension.
* Internet access for the one-time `pip install` (the application and tests themselves need none).
* Optional: Git, for GitHub submission.

Check your Python:

```powershell
py -3.12 --version     # or: py -3.11 --version
```

## 11. Windows setup instructions

1. Download the ZIP and **extract** it (right-click → *Extract All…*). Prefer a short path such as `C:\Projects\`.
2. Open VS Code → **File → Open Folder…** → choose the extracted `geospatial-file-measurement-api` folder (the folder that contains `app` and `requirements.txt`).
3. Open the integrated terminal: **Terminal → New Terminal** (PowerShell by default).

## 12. Virtual environment creation and activation

In the VS Code terminal, from the project root:

```powershell
# Create the virtual environment (use 3.11 if you have that instead)
py -3.12 -m venv .venv

# Activate it (PowerShell)
.\.venv\Scripts\Activate.ps1
```

Command Prompt (cmd) instead of PowerShell:

```bat
.venv\Scripts\activate.bat
```

If PowerShell says *"running scripts is disabled on this system"*, run this once and activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Your prompt should now start with `(.venv)`. In VS Code, press **Ctrl+Shift+P → "Python: Select Interpreter"** and choose the one inside `.venv`.

## 13. Dependency installation

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Verify the geospatial stack imports correctly:

```powershell
python -c "import fastapi, geopandas, shapely, pyproj, pyogrio; print('OK', geopandas.__version__, pyogrio.__gdal_version_string__)"
```

### Troubleshooting installation

| Problem | Fix |
|---|---|
| `Could not find a version that satisfies the requirement …` / it tries to compile from source | You are probably on an unsupported Python (3.13+/32-bit/old 3.8). Install **64-bit Python 3.11 or 3.12**, recreate `.venv`, upgrade pip (`python -m pip install --upgrade pip`) and retry. |
| `error: Microsoft Visual C++ 14.0 or greater is required` | A package is being built from source — see the row above. If it persists, install the *Microsoft Visual C++ Redistributable* (and, only if compiling is unavoidable, *Build Tools for Visual Studio*). |
| `pyproj.exceptions.CRSError` / `proj_create: Cannot find proj.db` | Another program (PostgreSQL/PostGIS, QGIS, OSGeo4W, Anaconda) has set `PROJ_DATA` / `PROJ_LIB`. Clear them for this session: `Remove-Item Env:PROJ_DATA -ErrorAction SilentlyContinue; Remove-Item Env:PROJ_LIB -ErrorAction SilentlyContinue`, or delete them in *System Properties → Environment Variables*. |
| `ImportError: DLL load failed` when importing `pyogrio` / `shapely` | Install the latest *Microsoft Visual C++ Redistributable (x64)* and restart VS Code. Make sure you are using the `.venv` interpreter (prompt shows `(.venv)`). |
| `ModuleNotFoundError: No module named 'app'` when running tests | Run commands from the **project root** (the folder containing `app/` and `pytest.ini`). |
| `'pip' is not recognized` | Use `python -m pip …` or `py -3.12 -m pip …`. |
| NumPy / pandas binary-compatibility errors after mixing installs | Delete `.venv` and recreate it, then reinstall from `requirements.txt` only (do not mix `conda` and `pip`). |
| Corporate proxy / offline | `pip install --proxy http://user:pass@host:port -r requirements.txt`, or download wheels on another machine with `pip download -r requirements.txt` and install with `pip install --no-index --find-links <folder> -r requirements.txt`. |

## 14. Running the FastAPI application

```powershell
uvicorn app.main:app --reload
```

The server starts at <http://127.0.0.1:8000>. The SQLite database is created automatically at `data\geospatial.db` (git-ignored). Stop the server with **Ctrl+C**.

If port 8000 is busy: `uvicorn app.main:app --reload --port 8001`.

Optional configuration — copy `.env.example` to `.env` (all settings have defaults):

| Variable | Default | Meaning |
|---|---|---|
| `GEO_DATABASE_PATH` | `data/geospatial.db` | SQLite file |
| `GEO_MAX_UPLOAD_MB` | `20` | Max upload size |
| `GEO_MAX_FEATURES` | `10000` | Max features per file |
| `GEO_MAX_ZIP_ENTRIES` | `200` | Max entries in a ZIP |
| `GEO_MAX_ZIP_UNCOMPRESSED_MB` | `200` | Max total expanded size of a ZIP |
| `GEO_MAX_ZIP_COMPRESSION_RATIO` | `200` | Max compression ratio for entries larger than 1 MiB |

## 15. Swagger UI at `/docs`

With the server running, open <http://127.0.0.1:8000/docs> (from PowerShell: `start http://127.0.0.1:8000/docs`). Use **POST /api/files/ → Try it out → Choose File → Execute** to upload `samples\sample_survey.kml`, copy the returned `id`, and try the two `GET` endpoints. ReDoc is at <http://127.0.0.1:8000/redoc>.

## 16. API endpoints and example responses

> **Note:** the numbers below are *illustrative* (rounded) to show the format. Real values are computed from the uploaded file. KML attribute names depend on the GDAL KML driver (typically `Name`, `description`, …).

### `GET /health`

```json
{ "status": "ok", "service": "geospatial-file-measurement-api", "version": "1.0.0" }
```

### `POST /api/files/` → `201 Created`

Multipart form field **`file`**.

```json
{
  "id": "3f2b8c1e5a7d4c9e8b1a2d3c4e5f6a7b",
  "filename": "sample_survey.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "warnings": []
}
```

### `GET /api/files/{id}/` → `200` (or `404`)

```json
{
  "id": "3f2b8c1e5a7d4c9e8b1a2d3c4e5f6a7b",
  "filename": "sample_survey.kml",
  "file_type": "kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "crs_name": "WGS 84",
  "crs_type": "geographic",
  "status": "COMPLETED",
  "warnings": [],
  "size_bytes": 1534,
  "uploaded_at": "2026-10-09T10:15:30.123456Z"
}
```

### `GET /api/files/{id}/measurements/?limit=&offset=` → `200` (or `404`)

`limit` and `offset` are optional; by default all features are returned.

```json
{
  "file_id": "3f2b8c1e5a7d4c9e8b1a2d3c4e5f6a7b",
  "filename": "sample_survey.kml",
  "source_crs": "EPSG:4326",
  "measurement_crs": "EPSG:32644",
  "feature_count": 3,
  "offset": 0,
  "returned_count": 3,
  "summary": { "MEASURED": 2, "NOT_REQUIRED": 1 },
  "features": [
    {
      "feature_index": 0,
      "feature_id": "0",
      "layer": "Sample survey (Chennai area)",
      "geometry_type": "Polygon",
      "geometry": { "type": "Polygon", "coordinates": [[[80.2, 13.0, 0.0], [80.21, 13.0, 0.0], [80.21, 13.01, 0.0], [80.2, 13.01, 0.0], [80.2, 13.0, 0.0]]] },
      "properties": { "Name": "Parcel A", "description": "Roughly 1.1 km x 1.1 km square plot" },
      "measurement_status": "MEASURED",
      "measurement_crs": "EPSG:32644",
      "area": 1200000.0,
      "area_unit": "square_metres",
      "length": null,
      "length_unit": null,
      "message": null,
      "warnings": []
    },
    {
      "feature_index": 1,
      "feature_id": "1",
      "layer": "Sample survey (Chennai area)",
      "geometry_type": "LineString",
      "geometry": { "type": "LineString", "coordinates": [[80.19, 13.0, 0.0], [80.19, 13.05, 0.0]] },
      "properties": { "Name": "Access road" },
      "measurement_status": "MEASURED",
      "measurement_crs": "EPSG:32644",
      "area": null,
      "area_unit": null,
      "length": 5530.0,
      "length_unit": "metres",
      "message": null,
      "warnings": []
    },
    {
      "feature_index": 2,
      "feature_id": "2",
      "layer": "Sample survey (Chennai area)",
      "geometry_type": "Point",
      "geometry": { "type": "Point", "coordinates": [80.205, 13.005, 0.0] },
      "properties": { "Name": "Survey marker" },
      "measurement_status": "NOT_REQUIRED",
      "measurement_crs": null,
      "area": null,
      "area_unit": null,
      "length": null,
      "length_unit": null,
      "message": "No area or length is defined for point geometries.",
      "warnings": []
    }
  ]
}
```

**Geometry format.** `geometry` is a standard GeoJSON geometry object **in the source CRS** — coordinates are *not* reprojected (for a projected Shapefile they are in that CRS's units; KML Z values are kept). Properties are returned as parsed from the file, with NaN/NaT converted to `null`.

### Error format (all errors)

```json
{ "error": { "code": "FILE_NOT_FOUND", "message": "No file exists with the given ID.", "details": { "file_id": "abc" } } }
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `EMPTY_FILE` | zero-byte upload |
| 400 | `INVALID_ZIP` | not a readable ZIP / corrupt compressed data |
| 400 | `UNSAFE_ARCHIVE` | path traversal, absolute path, symlink, encrypted entry, too many entries, zip-bomb ratio |
| 404 | `FILE_NOT_FOUND` | unknown ID |
| 413 | `FILE_TOO_LARGE` / `ARCHIVE_TOO_LARGE` / `TOO_MANY_FEATURES` | configured limits exceeded |
| 415 | `UNSUPPORTED_FILE_TYPE` | extension is not `.kml` / `.zip` |
| 422 | `INVALID_KML` | malformed XML, wrong root, binary content, DOCTYPE/ENTITY |
| 422 | `MISSING_SHAPEFILE_COMPONENTS` | `.shp`, `.shx` or `.dbf` missing (`details.missing_components`) |
| 422 | `MULTIPLE_DATASETS` | more than one Shapefile in the ZIP |
| 422 | `UNREADABLE_DATASET` | GDAL cannot read the data (corrupt) |
| 422 | `VALIDATION_ERROR` | bad request (e.g. missing `file` field, invalid `limit`) |
| 500 | `INTERNAL_ERROR` | unexpected error (details are logged server-side only) |

## 17. cURL examples

Use `curl.exe` in PowerShell (plain `curl` is an alias for another command there). Run from the project root with the server running.

```powershell
# Upload a KML file
curl.exe -X POST "http://127.0.0.1:8000/api/files/" -F "file=@samples\sample_survey.kml"

# Upload a zipped Shapefile (create it first: python samples\generate_sample_shapefile.py)
curl.exe -X POST "http://127.0.0.1:8000/api/files/" -F "file=@samples\sample_parcels.zip"

# File information (replace <ID> with the id returned by the upload)
curl.exe "http://127.0.0.1:8000/api/files/<ID>/"

# Measurements (optionally ?limit=10&offset=0)
curl.exe "http://127.0.0.1:8000/api/files/<ID>/measurements/"

# Health check
curl.exe "http://127.0.0.1:8000/health"
```

Command Prompt / macOS / Linux use the same commands (`curl` instead of `curl.exe`, `/` instead of `\` in paths).

## 18. Running the automated tests

With the virtual environment active, from the project root:

```powershell
pytest              # whole suite
pytest -v           # verbose
pytest tests\test_crs.py            # one file
pytest -k "persist or security"     # by keyword
```

The tests need no internet, no pre-existing uploads and never touch your development database: every test gets its own temporary SQLite file (`tmp_path`). Test data is generated on the fly (KML text and Shapefiles written with GeoPandas).

| File | Covers |
|---|---|
| `test_health.py` | health endpoint |
| `test_upload.py` | successful KML and ZIP-Shapefile upload, nested ZIP folder, filename sanitising |
| `test_file_information.py` | file info, `404` for unknown ID |
| `test_measurements.py` | polygon area, line length, points, response structure, pagination, null/empty and unsupported geometries, invalid-polygon repair |
| `test_crs.py` | UTM zone selection, EPSG:4326 → UTM before measuring, US-survey and international feet, Web Mercator, geodesic fallback, antimeridian, out-of-range coordinates, missing CRS |
| `test_measurement_service.py` | geometry classification unit tests |
| `test_invalid_files.py` | wrong extensions, empty/corrupt KML, invalid ZIP, missing/corrupt/ambiguous Shapefiles, validation errors, feature limit |
| `test_security.py` | path traversal, symlinks, zip bombs, size and entry limits, encrypted entries, no path/traceback leaks, hostile filenames |
| `test_persistence.py` | results survive an application restart; isolated test databases |

**How expected values are verified.** `tests/helpers.py` contains independent reference formulas (ellipsoid radii of curvature written in plain `math`) so projected results are compared against a value that does not use PyProj. Because UTM is not equal-area (section 8), area tolerances are 0.5 % and length tolerances 0.2 %; metre/feet conversions are checked to 1e-6 or better.

## 19. Security considerations

**Implemented**

* **Extension and content validation** — extension whitelist; KML must parse as XML with a `<kml>` root; ZIP must be a real archive containing a complete Shapefile.
* **Safe filenames** — only a sanitised display name is stored; uploaded bytes are written to a fixed temp name.
* **ZIP hardening** — rejects absolute paths, drive letters, `..` segments, NUL bytes, symlinks, encrypted entries, excessive entry counts, excessive declared size and suspicious compression ratios; only whitelisted Shapefile parts are extracted, to fixed names, with a streamed byte budget (a ZIP header that lies about sizes cannot bypass the limit).
* **XML safety** — `DOCTYPE`/`ENTITY` declarations and binary/UTF-16 content are rejected, blocking entity-expansion tricks.
* **Resource limits** — upload size (header pre-check + streaming check), ZIP limits, feature count (enforced while reading, so memory is bounded).
* **Temp files** — private temp directory per request, always deleted (`finally` semantics via `with`).
* **No information leakage** — GDAL/pyogrio errors are logged server-side and replaced by generic messages; unexpected exceptions return a generic `500`; no tracebacks or paths in responses.
* **SQL injection** — all queries use bound parameters.
* **Untrusted CRS** — the declared CRS is parsed and classified; unusable or missing CRS yields explicit statuses, and coordinates outside the valid range of a geographic CRS are rejected rather than measured.

**Not implemented (out of scope)**: authentication/authorisation, rate limiting, virus scanning, HTTPS termination, per-user data isolation. Put the service behind a reverse proxy with these controls before exposing it publicly.

## 20. Assumptions and known limitations

* One Shapefile per ZIP; the ZIP must contain `.shp`, `.shx`, `.dbf` (`.prj` is optional — without it the CRS is missing). `.kmz` is not supported.
* KML coordinates are WGS 84 by specification. All KML layers (folders) are merged into one feature list in file order; `layer` records the origin.
* Shapefile attribute encoding follows GDAL's default (`.cpg` is extracted and honoured when present).
* Accuracy: UTM-based area ≈ ±0.2 %, length ≈ ±0.1 % (section 8). Very large features use the geodesic fallback.
* GeometryCollections and other exotic types are returned with `UNSUPPORTED_GEOMETRY` rather than being split into parts.
* Invalid polygons are measured *after* repair; the repaired area may differ from what a particular GIS would report for the invalid shape.
* Processing is synchronous; very large files hold a worker thread until finished.
* Uploads are not de-duplicated, and there is no delete/list endpoint.
* The measurements endpoint returns all features by default (use `limit`/`offset` for big files).
* The suite relies on the GDAL KML driver bundled with Pyogrio; attribute names for KML (`Name`, `description`, …) come from that driver.

## 21. Learning outcomes

* **REST API development** — resource-oriented URLs, correct status codes (`201`, `404`, `413`, `415`, `422`), multipart uploads, response models, OpenAPI docs, a consistent error contract, dependency injection and the app-factory pattern.
* **Geospatial formats** — KML (XML, always WGS 84, folders → layers) versus Shapefile (multi-file: `.shp` geometry, `.shx` index, `.dbf` attributes, `.prj` CRS), one geometry type per Shapefile, and GeoJSON as an output interchange format.
* **Geometry operations** — Shapely geometry types, validity, `make_valid`, holes and multi-part geometries, empty/null geometry handling, dropping Z.
* **CRS transformation** — why degrees cannot be used for area/length, geographic vs projected CRS, UTM zone selection, distortion and its limits, equal-area vs conformal projections, Web Mercator's area inflation, geodesic computation on an ellipsoid, axis order (`always_xy`), and never guessing a missing CRS.
* **Measurement units** — linear unit factors (metre, foot, US survey foot) and why areas scale with the *square* of the factor.
* **File validation** — treating every upload as hostile: content sniffing, size limits, zip-slip, zip bombs, symlinks, XML entity attacks, and leaking nothing in errors.
* **SQLite persistence** — schema design, transactions, foreign keys, parameterised queries, storing JSON documents, and testing persistence by "restarting" the app against the same file.
* **Automated testing** — fixtures with temporary databases, synthetic datasets, independently computed expected values, tolerances for projection error, security tests and unit vs API tests.

## 22. Future scope

*These are proposals — none of them is implemented.*

* Authentication, per-user file ownership, rate limiting.
* Asynchronous processing with a job queue and a `PROCESSING` / `FAILED` status for very large datasets.
* KMZ, GeoJSON, GeoPackage and multi-Shapefile ZIP support.
* An equal-area projection (e.g. a local Lambert Azimuthal Equal Area) for even more accurate polygon areas; checking a projected CRS's area of use.
* List/delete endpoints, file de-duplication by hash, data retention policy.
* Spatial indexing (SpatiaLite/PostGIS) and bounding-box queries; GeoJSON `FeatureCollection` output.
* Optional reprojection of returned geometries to EPSG:4326.
* Docker packaging, CI (GitHub Actions) and cloud deployment.

## 23. GitHub repository setup and submission

1. Create an empty **public** repository on <https://github.com/new> (e.g. `geospatial-file-measurement-api`). Do *not* add a README or `.gitignore` there (the project already has them).
2. In the VS Code terminal, from the project root:

   ```powershell
   git init
   git add .
   git status                      # confirm .venv, data\, .env are NOT listed
   git commit -m "Geospatial File Measurement API"
   git branch -M main
   git remote add origin https://github.com/<your-username>/geospatial-file-measurement-api.git
   git push -u origin main
   ```

   If Git asks for credentials, sign in through the browser prompt or use a personal access token as the password.
3. Run `pytest` before pushing and make sure it passes on your machine.
4. Submit the repository URL. A good submission checklist:
   * [ ] README renders correctly on GitHub
   * [ ] `requirements.txt` installs on a clean virtual environment
   * [ ] `pytest` passes
   * [ ] no `.venv`, `data\`, `.env`, caches or uploaded files are committed
   * [ ] you can explain the CRS strategy (section 8) and its limitations in your own words

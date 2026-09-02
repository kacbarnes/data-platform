"""Create the local Polaris catalog idempotently using only the stdlib."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


BASE = os.environ["POLARIS_URL"]
REALM = os.environ["POLARIS_REALM"]
CATALOG = os.environ["POLARIS_CATALOG"]


def request(path, *, method="GET", token=None, body=None, form=None):
    headers = {"Polaris-Realm": REALM, "Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        return exc.code, json.loads(payload) if payload else None


_, token_response = request(
    "/api/catalog/v1/oauth/tokens",
    method="POST",
    form={
        "grant_type": "client_credentials",
        "client_id": os.environ["POLARIS_ROOT_CLIENT_ID"],
        "client_secret": os.environ["POLARIS_ROOT_CLIENT_SECRET"],
        "scope": "PRINCIPAL_ROLE:ALL",
    },
)
token = token_response["access_token"]

status, _ = request(f"/api/management/v1/catalogs/{CATALOG}", token=token)
if status == 200:
    print(f"Catalog {CATALOG!r} already exists; no change needed.")
else:
    status, response = request(
        "/api/management/v1/catalogs",
        method="POST",
        token=token,
        body={
            "catalog": {
                "name": CATALOG,
                "type": "INTERNAL",
                "readOnly": False,
                "properties": {
                    "default-base-location": f"s3://{os.environ['ICEBERG_BUCKET']}"
                },
                "storageConfigInfo": {
                    "storageType": "S3",
                    "allowedLocations": [f"s3://{os.environ['ICEBERG_BUCKET']}"],
                    "endpoint": "http://localhost:9000",
                    "endpointInternal": "http://rustfs:9000",
                    "pathStyleAccess": True,
                    "region": "us-west-2",
                },
            }
        },
    )
    if status not in (200, 201):
        raise SystemExit(f"Catalog creation failed ({status}): {response}")
    print(f"Created Polaris catalog {CATALOG!r}.")


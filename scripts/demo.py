"""End-to-end demo against a running server (standard library only).

    python scripts/demo.py                       # uses samples/request.json
    python scripts/demo.py --recipients 500      # generates a synthetic batch
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def call(method: str, url: str, body: dict | None = None) -> tuple[int, bytes, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--recipients", type=int, help="generate N synthetic recipients")
    args = parser.parse_args()

    payload = json.loads((ROOT / "samples" / "request.json").read_text())
    if args.recipients:
        payload["recipients"] = [
            {"name": f"Participant {i:04d}", "email": f"participant{i}@example.com"}
            for i in range(1, args.recipients + 1)
        ]

    status, raw, _ = call("POST", f"{args.base_url}/api/v1/jobs", payload)
    job = json.loads(raw)
    print(f"POST /jobs -> {status}")
    if status != 202:
        print(json.dumps(job, indent=2))
        return
    print(f"job id: {job['id']}  invalid recipients: {job['invalid_recipients']}")

    while True:
        job = json.loads(call("GET", f"{args.base_url}{job['links']['self']}")[1])
        p = job["progress"]
        print(
            f"  {job['status']:<22} {p['percent_complete']:5.1f}%  "
            f"generated={p['generated']} failed={p['failed']} invalid={p['invalid']}"
        )
        if job["status"] not in ("pending", "processing"):
            break
        time.sleep(0.5)

    status, raw, _ = call("GET", f"{args.base_url}{job['links']['download_zip']}")
    if status == 200:
        out = ROOT / "output" / f"certificates_{job['id']}.zip"
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(raw)
        print(f"ZIP saved to {out}")
    else:
        print(f"ZIP download -> {status}: {raw.decode()}")


if __name__ == "__main__":
    main()

"""End-to-end check of a running deployment (used by CI).

Runs one complete interview through Caddy -> gateway -> Rasa -> action server
-> PostgreSQL, then checks the export, authentication and security headers.

    python scripts/smoke_test.py --base https://localhost --ca caddy-root.crt --token "$RESEARCHER_TOKEN"

TLS is verified against the given CA certificate (Caddy's local CA for a
localhost test); verification is never disabled.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

APP_HEADERS = {
    "content-security-policy": "default-src 'self'",
    "strict-transport-security": "max-age=",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


class Client:
    def __init__(self, base: str, ca: str) -> None:
        if not base.startswith("https://"):
            raise SystemExit("--base must be an https:// URL")
        self.base = base.rstrip("/")
        self.ctx = ssl.create_default_context(cafile=ca)

    def request(
        self, method: str, path: str, *, token: str | None = None, json_body: object = None, form: dict | None = None
    ) -> tuple[int, dict, bytes]:
        headers = {"Accept": "application/json"}
        data = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)  # noqa: S310
        try:
            # The scheme is restricted to https:// in __init__.
            with urllib.request.urlopen(req, context=self.ctx, timeout=60) as resp:  # noqa: S310  # nosec B310
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()

    def json(self, method: str, path: str, expect: int = 200, **kwargs) -> dict:
        status, _, body = self.request(method, path, **kwargs)
        if status != expect:
            check(False, f"{method} {path}: expected {expect}, got {status}: {body[:300]!r}")
        return json.loads(body) if body else {}


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)
    print(f"ok   {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://localhost")
    parser.add_argument("--ca", required=True)
    parser.add_argument("--token", required=True, help="RESEARCHER_TOKEN")
    args = parser.parse_args()
    c = Client(args.base, args.ca)

    # Web app and headers ---------------------------------------------------
    status, headers, body = c.request("GET", "/")
    check(status == 200 and b"<html" in body.lower(), "participant web app is served over verified HTTPS")
    for name, expected in APP_HEADERS.items():
        check(expected in headers.get(name, ""), f"header {name} contains {expected!r}")
    check("server" not in headers, "no Server header")
    for path in ("/docs", "/openapi.json", "/.env", "/.git/config"):
        check(c.request("GET", path)[0] == 404, f"{path} is not exposed")

    ready = c.json("GET", "/api/ready")
    check(ready.get("status") == "ready" and ready.get("engine") == "rasa", "gateway ready with the Rasa engine")

    # Authentication ----------------------------------------------------------
    check(
        c.request("POST", "/api/researcher/participants", json_body={"count": 1})[0] == 401,
        "researcher API rejects requests without a token",
    )
    check(
        c.request("GET", "/api/researcher/export.csv", token="x" * 48)[0] == 401, "researcher API rejects a wrong token"
    )
    unknown_code = "not-a-real-participant-code"  # deliberately invalid; must be rejected
    check(c.request("GET", "/api/session", token=unknown_code)[0] == 401, "participant API rejects unknown links")

    # Invite and run one full interview -------------------------------------
    invited = c.json("POST", "/api/researcher/participants", expect=201, token=args.token, json_body={"count": 1})
    person = invited["participants"][0]
    code = urllib.parse.urlsplit(person["link"]).fragment.removeprefix("code=")
    check(len(code) >= 32 and "#code=" in person["link"], "invite link carries the secret in the URL fragment")
    check(c.json("GET", "/api/session", token=code)["status"] == "invited", "participant link works")

    session = c.json(
        "POST", "/api/session/consent", token=code, json_body={"agree": True, "adult": True, "native_english": True}
    )
    replies = iter(["I'm good, thanks", "a little nervous", "yes, ready"])
    turns = 0
    while session["prompt"]["kind"] != "end":
        turns += 1
        if turns > 30:
            check(False, "interview did not end within 30 turns")
        prompt = session["prompt"]
        text = next(replies, None) or f"Smoke-test answer {turns}: I was proud of my team but stressed."
        session = c.json("POST", "/api/session/answers", token=code, form={"prompt_id": prompt["id"], "text": text})
    check(session["status"] == "completed", f"interview completed in {turns} turns")
    check(
        c.request("POST", "/api/session/answers", token=code, form={"prompt_id": "end", "text": "x"})[0] == 409,
        "no answers accepted after the end",
    )

    # Export --------------------------------------------------------------------
    status, headers, body = c.request("GET", "/api/researcher/export.csv", token=args.token)
    check(status == 200 and headers.get("content-type", "").startswith("text/csv"), "CSV export works")
    rows = [r for r in csv.DictReader(io.StringIO(body.decode())) if r["participant"] == person["participant"]]
    questions = [r for r in rows if r["kind"] == "question"]
    check(len(questions) == 16, "16 interview answers exported")
    voices = {}
    for r in questions:
        voices.setdefault(r["block"], set()).add(r["voice"])
    check(
        all(len(v) == 1 for v in voices.values()) and len({next(iter(v)) for v in voices.values()}) == 4,
        "one voice per topic block, four different voices",
    )
    check(
        [next(iter(voices[b])) for b in dict.fromkeys(r["block"] for r in questions)] == person["voice_order"],
        "voice order follows the counterbalanced sequence",
    )

    # Withdrawal ----------------------------------------------------------------
    withdrawn = c.json("POST", "/api/session/withdraw", token=code)
    check(withdrawn["answers_deleted"] == len(rows), "withdrawal deletes every answer")
    check(c.request("GET", "/api/session", token=code)[0] == 410, "withdrawn link is closed")
    print("smoke test passed")


if __name__ == "__main__":
    main()

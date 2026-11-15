                                                                       """Read-only MongoDB connectivity diagnostic. Prints NO credentials.

Probes the configured MONGODB_URL with several auth sources / mechanisms so the
Phase 1 blocker (Atlas authentication failure) can be isolated safely.
"""
from __future__ import annotations

import re
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError


def read_url() -> str:
    for path in (Path("backend/.env"), Path(".env")):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*MONGODB_URL\s*=\s*(.+)$", line)
            if match:
                return match.group(1).strip().strip('"').strip("'")
    return ""


def probe(url: str, label: str, **kwargs) -> None:
    try:
        client = MongoClient(url, serverSelectionTimeoutMS=6000, **kwargs)
        client.admin.command("ping")
        names = client.list_database_names()
        print(f"{label:40s} OK   dbs={names[:8]}")
    except OperationFailure as exc:
        print(f"{label:40s} AUTH-FAIL code={getattr(exc, 'code', None)}")
    except ServerSelectionTimeoutError:
        print(f"{label:40s} TIMEOUT (network / Atlas IP allowlist)")
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        print(f"{label:40s} ERR {type(exc).__name__}: {str(exc)[:90]}")


def main() -> None:
    url = read_url()
    if not url:
        print("MONGODB_URL not set")
        return
    head = url.split("://", 1)[-1].split("?", 1)[0]
    print("scheme:", "mongodb+srv" if url.startswith("mongodb+srv") else "mongodb")
    print("has userinfo:", "@" in head)
    print("host part len:", len(head.split("@")[-1]))
    probe(url, "default (as written)")
    probe(url, "authSource=admin", authSource="admin")
    probe(url, "authSource=enterprise_ai_db", authSource="enterprise_ai_db")
    probe(url, "authMechanism=SCRAM-SHA-256", authMechanism="SCRAM-SHA-256")
    probe(url, "directConnection=True", directConnection=True)


if __name__ == "__main__":
    main()

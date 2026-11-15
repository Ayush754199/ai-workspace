"""Do values from local .env files appear in any committed git object?

Prints file names and variable names only — never the values themselves.
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

values = {}
for env_file in (".env", os.path.join("backend", ".env")):
    if not os.path.exists(env_file):
        continue
    with open(env_file, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"')
            if len(value) >= 12 and not value.startswith("<"):
                values[value] = key.strip()

print("local secret values loaded:", len(values))

objects = subprocess.run(["git", "rev-list", "--all", "--objects"], capture_output=True, text=True, check=False).stdout.splitlines()
seen = set()
findings = []
scanned = 0
for line in objects:
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        continue  # trees have no path on this line
    sha, path = parts
    if not path or path.startswith(".git"):
        continue
    if (sha, path) in seen:
        continue
    seen.add((sha, path))
    data = subprocess.run(["git", "cat-file", "-p", sha], capture_output=True, check=False).stdout
    scanned += 1
    for value, variable in values.items():
        # Only credential values count. ``.env.example`` legitimately carries
        # non-secret defaults (database name, a Space id), so a short generic
        # value there is not a leak.
        if len(value) < 20 or not any(marker in variable.upper() for marker in ("KEY", "TOKEN", "URL", "SECRET")):
            continue
        if value.encode() in data:
            findings.append((path, variable))

print("objects scanned:", scanned)
if not findings:
    print("RESULT: no local .env value appears in committed git history")
else:
    seen_pairs = sorted(set(findings))
    print("RESULT: local .env values DO appear in committed history (values not printed):")
    for path, variable in seen_pairs:
        print(f"  {path} -> {variable}".encode("ascii", "replace").decode("ascii"))

"""A baseline pass and an assertion-failing mutation are both required."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    spec = json.loads(Path(__file__).with_name("specs.json").read_text())[sys.argv[1]]
    originals = {name: Path(name).read_bytes() for name, _, _ in spec["edits"]}
    with tempfile.TemporaryDirectory(prefix="bench-guard-") as temporary:
        junit = Path(temporary) / "result.xml"
        command = [sys.executable, "-m", "pytest", spec["test"], "-q", "-n", "0", "--junitxml", str(junit)]
        if subprocess.run(command).returncode != 0:
            return 2
        try:
            for name, old, new in spec["edits"]:
                path = Path(name)
                source = path.read_text()
                if source.count(old) != 1:
                    raise RuntimeError(f"mutation anchor no longer unique: {name}: {old!r}")
                path.write_text(source.replace(old, new))
            junit.unlink()
            result = subprocess.run(command)
            if result.returncode != 1 or not junit.exists():
                return 3
            tree = ET.parse(junit)
            if not tree.findall(".//failure") or tree.findall(".//error"):
                return 4
            return 0
        finally:
            for name, source in originals.items():
                Path(name).write_bytes(source)


if __name__ == "__main__":
    raise SystemExit(main())

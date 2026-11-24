"""Check the simulator/ and web/ Python trees for undefined names with Pyflakes.

Pyflakes checks files independently and misses dynamically created runtime names.
"""

import ast
from pathlib import Path

from pyflakes.checker import Checker
from pyflakes.messages import UndefinedExport, UndefinedLocal, UndefinedName


def test_no_undefined_names():
    repo_root = Path(__file__).resolve().parents[1]
    failures = []
    undefined_types = (UndefinedName, UndefinedExport, UndefinedLocal)
    for root in (repo_root / "simulator", repo_root / "web"):
        for path in sorted(root.rglob("*.py")):
            relative_path = path.relative_to(repo_root)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for message in Checker(tree, filename=str(path)).messages:
                if isinstance(message, undefined_types):
                    args = message.message_args
                    name = args[0] if isinstance(args, tuple) else args
                    failures.append(f"{relative_path}:{message.lineno}:{name}")
    assert not failures, "\n".join(failures)

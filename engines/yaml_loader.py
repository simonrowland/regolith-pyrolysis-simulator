"""YAML 1.2 loader that does not import the simulator package."""

import re

import yaml


_YAML_BASE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class YAML12SafeLoader(_YAML_BASE_LOADER):
    """SafeLoader with YAML 1.2 core-schema implicit booleans."""

    # Copy the resolver table so adding the YAML 1.2 boolean resolver does not
    # mutate the parent loader used by other YAML readers.
    yaml_implicit_resolvers = {
        key: [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag != "tag:yaml.org,2002:bool"
        ]
        for key, resolvers in _YAML_BASE_LOADER.yaml_implicit_resolvers.items()
    }


YAML12SafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)

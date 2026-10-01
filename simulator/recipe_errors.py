"""Typed failures for malformed recipe inputs crossing engine boundaries."""


class MalformedRecipeError(ValueError):
    """Recipe data is malformed and should be scored as an invalid recipe."""

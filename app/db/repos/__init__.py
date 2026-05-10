"""Repository layer. Owns all MongoDB queries.

Routes never touch MongoDB directly — they call services, which call these
repos. Repos return Pydantic domain models with Mongo internals (``_id``,
cursor wrappers) projected away.
"""

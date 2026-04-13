"""Shared enum types."""

from enum import Enum


class ActionType(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    REFACTOR = "refactor"
    MOVE = "move"
    RENAME = "rename"


class EntityType(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"
    CONSTANT = "constant"
    GLOBAL_VAR = "global_var"


class EntityRole(str, Enum):
    PRIMARY = "primary"
    MODIFIED = "modified"
    REFERENCED = "referenced"


class RelationType(str, Enum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    INSTANTIATES = "instantiates"
    DECORATES = "decorates"
    COMPOSED_OF = "composed_of"
    OVERRIDES = "overrides"


class Provenance(str, Enum):
    AGENT_DECLARED = "agent_declared"
    AST_DERIVED = "ast_derived"
    INFERRED = "inferred"


class UsagePattern(str, Enum):
    DIRECT_CALL = "direct_call"
    INHERITANCE = "inheritance"
    CONFIG = "config"
    DECORATOR = "decorator"
    CONTEXT_MANAGER = "context_manager"


__all__ = [
    "ActionType",
    "EntityRole",
    "EntityType",
    "Provenance",
    "RelationType",
    "UsagePattern",
]

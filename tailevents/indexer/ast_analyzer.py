"""Python AST-based source analyzer."""

import ast
import hashlib
from typing import Any, Optional

from tailevents.models.enums import RelationType, UsagePattern


class ASTAnalyzer:
    """Analyze Python source code with the standard ast module."""

    def extract_entities(self, source: str, file_path: str) -> list[dict[str, Any]]:
        tree = self._parse(source)
        if tree is None:
            return []

        extractor = _EntityExtractor(source=source, file_path=file_path)
        extractor.visit(tree)
        return extractor.entities

    def extract_relations(
        self,
        source: str,
        file_path: str,
        known_entities: dict[str, str],
        entity_files: Optional[dict[str, str]] = None,
    ) -> list[dict[str, str]]:
        tree = self._parse(source)
        if tree is None:
            return []

        extractor = _RelationExtractor(
            tree=tree,
            source=source,
            file_path=file_path,
            known_entities=known_entities,
            entity_files=entity_files or {},
        )
        extractor.visit(tree)
        return extractor.relations

    def extract_external_refs(
        self,
        source: str,
        file_path: str,
        known_entities: dict[str, str],
        entity_files: Optional[dict[str, str]] = None,
    ) -> list[dict[str, str]]:
        tree = self._parse(source)
        if tree is None:
            return []

        extractor = _ExternalRefExtractor(
            tree=tree,
            source=source,
            file_path=file_path,
            known_entities=known_entities,
            entity_files=entity_files or {},
        )
        extractor.visit(tree)
        return extractor.external_refs

    def extract_imports(self, source: str) -> list[dict[str, Optional[str]]]:
        tree = self._parse(source)
        if tree is None:
            return []

        imports: list[dict[str, Optional[str]]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        {
                            "module": None,
                            "name": alias.name,
                            "alias": alias.asname,
                            "qualified_name": alias.name,
                        }
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    qualified_name = alias.name if not module else f"{module}.{alias.name}"
                    imports.append(
                        {
                            "module": node.module,
                            "name": alias.name,
                            "alias": alias.asname,
                            "qualified_name": qualified_name,
                        }
                    )
        return imports

    def parse(self, source: str) -> Optional[ast.AST]:
        """Parse source and return an AST or None on SyntaxError."""

        return self._parse(source)

    def _parse(self, source: str) -> Optional[ast.AST]:
        try:
            return ast.parse(source)
        except SyntaxError:
            return None


class _EntityExtractor(ast.NodeVisitor):
    def __init__(self, source: str, file_path: str):
        self._source = source
        self._file_path = file_path
        self._container_stack: list[tuple[str, str]] = []
        self.entities: list[dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        qualified_name = self._qualified_name(node.name)
        normalized_body = self._normalized_body(node)
        self.entities.append(
            {
                "name": node.name,
                "qualified_name": qualified_name,
                "entity_type": "class",
                "signature": self._class_signature(node),
                "params": [],
                "return_type": None,
                "docstring": ast.get_docstring(node),
                "line_range": self._line_range(node),
                "body_hash": self._body_hash(normalized_body),
                "normalized_body": normalized_body,
                "file_path": self._file_path,
            }
        )
        self._container_stack.append((node.name, "class"))
        self.generic_visit(node)
        self._container_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_function(node, is_async=True)

    def _visit_function(self, node: ast.AST, is_async: bool) -> None:
        function_node = node
        name = function_node.name
        parent_kind = self._container_stack[-1][1] if self._container_stack else None
        entity_type = "method" if parent_kind == "class" else "function"
        qualified_name = self._qualified_name(name)
        normalized_body = self._normalized_body(function_node)
        self.entities.append(
            {
                "name": name,
                "qualified_name": qualified_name,
                "entity_type": entity_type,
                "signature": self._function_signature(function_node, is_async=is_async),
                "params": self._params(function_node.args),
                "return_type": self._annotation(function_node.returns),
                "docstring": ast.get_docstring(function_node),
                "line_range": self._line_range(function_node),
                "body_hash": self._body_hash(normalized_body),
                "normalized_body": normalized_body,
                "file_path": self._file_path,
            }
        )
        self._container_stack.append((name, entity_type))
        self.generic_visit(function_node)
        self._container_stack.pop()

    def _qualified_name(self, name: str) -> str:
        if not self._container_stack:
            return name
        return ".".join([part[0] for part in self._container_stack] + [name])

    def _normalized_body(self, node: ast.AST) -> str:
        body = list(getattr(node, "body", []))
        if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant
        ) and isinstance(body[0].value.value, str):
            body = body[1:]
        normalized = [ast.dump(item, annotate_fields=True, include_attributes=False) for item in body]
        return "\n".join(normalized)

    def _body_hash(self, normalized_body: str) -> str:
        return hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()

    def _line_range(self, node: ast.AST) -> Optional[tuple[int, int]]:
        lineno = getattr(node, "lineno", None)
        end_lineno = getattr(node, "end_lineno", None)
        if lineno is None or end_lineno is None:
            return None
        return (int(lineno), int(end_lineno))

    def _annotation(self, annotation: Optional[ast.AST]) -> Optional[str]:
        if annotation is None:
            return None
        return ast.unparse(annotation)

    def _function_signature(self, node: ast.AST, is_async: bool) -> str:
        prefix = "async def" if is_async else "def"
        signature = f"{prefix} {node.name}({ast.unparse(node.args)})"
        if node.returns is not None:
            signature += f" -> {ast.unparse(node.returns)}"
        return signature

    def _class_signature(self, node: ast.ClassDef) -> str:
        if not node.bases:
            return f"class {node.name}"
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})"

    def _params(self, args: ast.arguments) -> list[dict[str, Optional[str]]]:
        params: list[dict[str, Optional[str]]] = []

        positional = list(args.posonlyargs) + list(args.args)
        positional_defaults = [None] * (len(positional) - len(args.defaults)) + list(
            args.defaults
        )
        for argument, default in zip(positional, positional_defaults):
            params.append(
                {
                    "name": argument.arg,
                    "type_hint": self._annotation(argument.annotation),
                    "default": ast.unparse(default) if default is not None else None,
                    "description": None,
                }
            )

        if args.vararg is not None:
            params.append(
                {
                    "name": args.vararg.arg,
                    "type_hint": self._annotation(args.vararg.annotation),
                    "default": None,
                    "description": None,
                }
            )

        for argument, default in zip(args.kwonlyargs, args.kw_defaults):
            params.append(
                {
                    "name": argument.arg,
                    "type_hint": self._annotation(argument.annotation),
                    "default": ast.unparse(default) if default is not None else None,
                    "description": None,
                }
            )

        if args.kwarg is not None:
            params.append(
                {
                    "name": args.kwarg.arg,
                    "type_hint": self._annotation(args.kwarg.annotation),
                    "default": None,
                    "description": None,
                }
            )

        return params


class _ResolutionVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        tree: ast.AST,
        source: str,
        file_path: str,
        known_entities: dict[str, str],
        entity_files: dict[str, str],
    ):
        self._source = source
        self._file_path = file_path
        self._known_entities = known_entities
        self._entity_files = entity_files
        self._entity_stack: list[str] = []
        self._container_stack: list[tuple[str, str]] = []
        self._class_stack: list[str] = []
        self._name_index = self._build_name_index(known_entities)
        self._same_file_index = self._build_file_name_index(entity_files, file_path)
        self._workspace_modules = self._build_workspace_modules(entity_files)
        self._import_aliases = self._build_import_aliases(tree)
        self._seen: set[tuple[str, str, str]] = set()

    def _build_import_aliases(self, tree: ast.AST) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local_name = alias.asname or alias.name.split(".", 1)[0]
                    aliases[local_name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    qualified_name = alias.name if not module else f"{module}.{alias.name}"
                    aliases[alias.asname or alias.name] = qualified_name
        return aliases

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        qualified_name = self._qualified_name(node.name)
        self._entity_stack.append(qualified_name)
        self._container_stack.append((node.name, "class"))
        self._class_stack.append(qualified_name)
        self.generic_visit(node)
        self._class_stack.pop()
        self._container_stack.pop()
        self._entity_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_function(node.name, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_function(node.name, node)

    def _visit_function(self, name: str, node: ast.AST) -> None:
        qualified_name = self._qualified_name(name)
        self._entity_stack.append(qualified_name)
        kind = "method" if self._container_stack and self._container_stack[-1][1] == "class" else "function"
        self._container_stack.append((name, kind))
        self.generic_visit(node)
        self._container_stack.pop()
        self._entity_stack.pop()

    def _qualified_name(self, name: str) -> str:
        if not self._container_stack:
            return name
        return ".".join([part[0] for part in self._container_stack] + [name])

    def _expr_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._expr_name(node.value)
            return node.attr if not base else f"{base}.{node.attr}"
        return ""

    def _normalize_import_alias(self, raw_name: str) -> str:
        if not raw_name:
            return raw_name
        head, _, tail = raw_name.partition(".")
        mapped = self._import_aliases.get(head)
        if mapped is None:
            return raw_name
        return mapped if not tail else f"{mapped}.{tail}"

    def _resolve_target(
        self, raw_name: str, current_class: Optional[str]
    ) -> Optional[str]:
        if not raw_name:
            return None
        if raw_name in self._known_entities:
            return raw_name

        if raw_name.startswith("self.") and current_class is not None:
            candidate = f"{current_class}.{raw_name.split('.', 1)[1]}"
            if candidate in self._known_entities:
                return candidate
            method_candidate = f"{current_class}.{raw_name.rsplit('.', 1)[-1]}"
            if method_candidate in self._known_entities:
                return method_candidate

        normalized_name = self._normalize_import_alias(raw_name)
        if normalized_name != raw_name:
            if normalized_name in self._known_entities:
                return normalized_name
            alias_short_name = normalized_name.rsplit(".", 1)[-1]
            alias_candidate = self._name_index.get(alias_short_name)
            if alias_candidate is not None:
                return alias_candidate

        short_name = raw_name.rsplit(".", 1)[-1]
        file_candidate = self._same_file_index.get(short_name)
        if file_candidate is not None:
            return file_candidate

        candidate = self._name_index.get(short_name)
        if candidate is not None:
            return candidate
        return None

    def _build_name_index(self, known_entities: dict[str, str]) -> dict[str, Optional[str]]:
        index: dict[str, Optional[str]] = {}
        for qualified_name in known_entities:
            short_name = qualified_name.rsplit(".", 1)[-1]
            if short_name not in index:
                index[short_name] = qualified_name
            elif index[short_name] != qualified_name:
                index[short_name] = None
        return index

    def _build_file_name_index(
        self,
        entity_files: dict[str, str],
        file_path: str,
    ) -> dict[str, Optional[str]]:
        index: dict[str, Optional[str]] = {}
        for qualified_name, candidate_file_path in entity_files.items():
            if candidate_file_path != file_path:
                continue
            short_name = qualified_name.rsplit(".", 1)[-1]
            if short_name not in index:
                index[short_name] = qualified_name
            elif index[short_name] != qualified_name:
                index[short_name] = None
        return index

    def _build_workspace_modules(self, entity_files: dict[str, str]) -> set[str]:
        modules: set[str] = set()
        for file_path in entity_files.values():
            normalized = file_path.replace("\\", "/")
            if not normalized.endswith(".py"):
                continue
            stem = normalized[:-3]
            parts = [part for part in stem.split("/") if part]
            if not parts:
                continue
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            module_name = ".".join(parts)
            modules.add(module_name)
            for index in range(1, len(parts)):
                modules.add(".".join(parts[:index]))
        return modules

    def _is_workspace_module(self, qualified_name: str) -> bool:
        parts = qualified_name.split(".")
        for index in range(len(parts), 0, -1):
            candidate = ".".join(parts[:index])
            if candidate in self._workspace_modules:
                return True
        return False

    def _add_relation(self, source_qname: str, target_qname: str, relation_type: str) -> None:
        raise NotImplementedError


class _RelationExtractor(_ResolutionVisitor):
    def __init__(
        self,
        *,
        tree: ast.AST,
        source: str,
        file_path: str,
        known_entities: dict[str, str],
        entity_files: dict[str, str],
    ):
        super().__init__(
            tree=tree,
            source=source,
            file_path=file_path,
            known_entities=known_entities,
            entity_files=entity_files,
        )
        self.relations: list[dict[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        qualified_name = self._qualified_name(node.name)
        for base in node.bases:
            target_qname = self._resolve_target(
                ast.unparse(base),
                current_class=qualified_name,
            )
            if target_qname is not None:
                self._add_relation(qualified_name, target_qname, RelationType.INHERITS.value)
        super().visit_ClassDef(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if self._entity_stack:
            target_qname = self._resolve_target(
                self._expr_name(node.func),
                current_class=self._class_stack[-1] if self._class_stack else None,
            )
            if target_qname is not None:
                self._add_relation(
                    self._entity_stack[-1], target_qname, RelationType.CALLS.value
                )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> Any:
        if self._entity_stack:
            for alias in node.names:
                target_qname = self._resolve_target(alias.name, current_class=None)
                if target_qname is not None:
                    self._add_relation(
                        self._entity_stack[-1], target_qname, RelationType.IMPORTS.value
                    )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        if self._entity_stack:
            module = node.module or ""
            for alias in node.names:
                raw_name = alias.name if not module else f"{module}.{alias.name}"
                target_qname = self._resolve_target(raw_name, current_class=None)
                if target_qname is not None:
                    self._add_relation(
                        self._entity_stack[-1], target_qname, RelationType.IMPORTS.value
                    )
        self.generic_visit(node)

    def _visit_function(self, name: str, node: ast.AST) -> None:
        qualified_name = self._qualified_name(name)
        if (
            self._class_stack
            and self._container_stack
            and self._container_stack[-1][1] == "class"
            and len(self._class_stack) == 1
        ):
            self._add_relation(
                self._class_stack[-1],
                qualified_name,
                RelationType.COMPOSED_OF.value,
            )
        super()._visit_function(name, node)

    def _add_relation(self, source_qname: str, target_qname: str, relation_type: str) -> None:
        key = (source_qname, target_qname, relation_type)
        if key in self._seen:
            return
        self._seen.add(key)
        self.relations.append(
            {
                "source_qname": source_qname,
                "target_qname": target_qname,
                "relation_type": relation_type,
            }
        )


class _ExternalRefExtractor(_ResolutionVisitor):
    def __init__(
        self,
        *,
        tree: ast.AST,
        source: str,
        file_path: str,
        known_entities: dict[str, str],
        entity_files: dict[str, str],
    ):
        super().__init__(
            tree=tree,
            source=source,
            file_path=file_path,
            known_entities=known_entities,
            entity_files=entity_files,
        )
        self.external_refs: list[dict[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        qualified_name = self._qualified_name(node.name)
        for base in node.bases:
            external_ref = self._build_external_ref(
                raw_name=ast.unparse(base),
                usage_pattern=UsagePattern.INHERITANCE.value,
                current_class=qualified_name,
            )
            if external_ref is not None:
                self._add_external_ref(external_ref)
        super().visit_ClassDef(node)

    def visit_Call(self, node: ast.Call) -> Any:
        external_ref = self._build_external_ref(
            raw_name=self._expr_name(node.func),
            usage_pattern=UsagePattern.DIRECT_CALL.value,
            current_class=self._class_stack[-1] if self._class_stack else None,
        )
        if external_ref is not None:
            self._add_external_ref(external_ref)
        self.generic_visit(node)

    def _build_external_ref(
        self,
        *,
        raw_name: str,
        usage_pattern: str,
        current_class: Optional[str],
    ) -> Optional[dict[str, str]]:
        if not raw_name or raw_name.startswith("self."):
            return None
        if self._resolve_target(raw_name, current_class=current_class) is not None:
            return None

        normalized_name = self._normalize_import_alias(raw_name)
        if normalized_name == raw_name and "." not in raw_name:
            return None
        if self._is_workspace_module(normalized_name):
            return None

        parts = normalized_name.split(".")
        if len(parts) < 2:
            return None

        package = parts[0]
        symbol = parts[-1]
        if not package or not symbol:
            return None
        return {
            "package": package,
            "symbol": symbol,
            "usage_pattern": usage_pattern,
        }

    def _add_external_ref(self, external_ref: dict[str, str]) -> None:
        key = (
            external_ref["package"],
            external_ref["symbol"],
            external_ref["usage_pattern"],
        )
        if key in self._seen:
            return
        self._seen.add(key)
        self.external_refs.append(external_ref)

    def _add_relation(self, source_qname: str, target_qname: str, relation_type: str) -> None:
        _ = source_qname
        _ = target_qname
        _ = relation_type


__all__ = ["ASTAnalyzer"]

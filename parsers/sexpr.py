"""Small, bounded reader for KiCad's nested S-expressions.

Keep line numbers for report evidence. Unknown sections remain structured and
are ignored by component parsers. Never evaluate expressions as Python code.
"""

from dataclasses import dataclass, field
import re


class ParseError(ValueError):
    pass


@dataclass
class Node:
    tag: str
    line: int
    values: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    start: int = 0
    end: int = 0

    def all(self, tag: str) -> list["Node"]:
        return [node for node in self.children if node.tag == tag]

    def first(self, tag: str) -> "Node | None":
        return next((node for node in self.children if node.tag == tag), None)

    def get(self, tag: str, default: str = "") -> str:
        child = self.first(tag)
        return child.values[0] if child and child.values else default


TOKEN = re.compile(r'\s+|;[^\n]*|[()]|"(?:\\[\s\S]|[^"\\])*"|[^\s()";]+')
ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
MAX_TOKENS = 2_000_000
MAX_DEPTH = 128


def parse(text: str, expected_root: str) -> Node:
    stack: list[Node] = []
    root = None
    line = 1
    offset = 0
    tokens = 0
    for match in TOKEN.finditer(text):
        if match.start() != offset:
            raise ParseError(f"Invalid or unterminated string near line {line}.")
        token = match.group()
        offset = match.end()
        token_line = line
        line += token.count("\n")
        if token.isspace() or token.startswith(";"):
            continue
        tokens += 1
        if tokens > MAX_TOKENS:
            raise ParseError("File exceeds the parser token limit.")
        if token == "(":
            if len(stack) >= MAX_DEPTH:
                raise ParseError("File exceeds the parser nesting limit.")
            if stack and not stack[-1].tag:
                raise ParseError(f"Missing expression name at line {token_line}.")
            node = Node("", token_line, start=match.start())
            if stack:
                stack[-1].children.append(node)
            elif root is not None:
                raise ParseError(f"Multiple root expressions at line {token_line}.")
            else:
                root = node
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ParseError(f"Unexpected closing parenthesis at line {token_line}.")
            closed = stack.pop()
            if not closed.tag:
                raise ParseError(f"Unexpected closing parenthesis at line {token_line}.")
            closed.end = match.end()
        else:
            if not stack:
                raise ParseError(f"Text outside an expression at line {token_line}.")
            if token.startswith('"'):
                token = re.sub(r"\\(.)", lambda m: ESCAPES.get(m[1], m[0]), token[1:-1])
            if not stack[-1].tag:
                stack[-1].tag = token
            else:
                stack[-1].values.append(token)
    if offset != len(text) or stack or root is None:
        raise ParseError("Incomplete or empty KiCad file.")
    if root.tag != expected_root:
        raise ParseError(f"Expected ({expected_root} ...), found ({root.tag} ...).")
    return root


def properties(node: Node) -> dict[str, str]:
    result = {}
    for prop in node.all("property"):
        if len(prop.values) < 2:
            raise ParseError(f"Incomplete property at line {prop.line}.")
        name, value = prop.values[:2]
        if name in result and result[name] != value:
            raise ParseError(f"Conflicting {name!r} properties at line {prop.line}.")
        result[name] = value
    return result


def flag(node: Node, name: str, default: bool) -> bool:
    value = node.get(name, "yes" if default else "no")
    if value not in ("yes", "no"):
        raise ParseError(f"Invalid {name} flag at line {node.line}.")
    return value == "yes"


def unit_number(node: Node, default: int = 1) -> int:
    value = node.get("unit", str(default))
    if len(value) > 8 or not value.isascii() or not value.isdigit() or int(value) < 1:
        raise ParseError(f"Invalid symbol unit at line {node.line}.")
    return int(value)

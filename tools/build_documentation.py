#!/usr/bin/env python3
"""Generate source and HTTP references without importing services or reading runtime data."""
import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("ClauseESG", "ExtractESG", "ConsoleESG", "database", "PlatESG", "tools", "scripts")
SKIP = {".local", ".venv", "node_modules", "__pycache__", "build", "dist", ".pytest_cache"}
EXTENSIONS = {".py", ".mjs", ".ts", ".tsx", ".sh", ".swift", ".toml", ".sql", ".css", ".html", ".json", ".yaml", ".yml"}
VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}


def sources():
    import os
    for name in TREES:
        for directory, children, names in os.walk(ROOT / name):
            children[:] = sorted(d for d in children if d not in SKIP and not d.endswith(".egg-info"))
            for filename in sorted(names):
                path = Path(directory) / filename
                relative = path.relative_to(ROOT)
                if "schemas" in relative.parts or "fixtures" in relative.parts or relative.as_posix().startswith("ClauseESG/clauses/"):
                    continue
                if path.suffix in EXTENSIONS and not path.name.endswith(".d.ts"):
                    yield path


def literal(node, environment):
    if isinstance(node, ast.Name) and node.id in environment:
        return environment[node.id]
    if isinstance(node, ast.JoinedStr):
        return "".join(str(literal(item.value, environment)) if isinstance(item, ast.FormattedValue)
                       else item.value for item in node.values)
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)


def route_records(path, tree):
    prefixes = {}
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "APIRouter":
            prefix = next((literal(k.value, {}) for k in node.value.keywords if k.arg == "prefix"), "")
            for target in node.targets:
                prefixes[ast.unparse(target)] = prefix

    def emit(call, handler, line, environment):
        if not call.args:
            return
        address = literal(call.args[0], environment)
        if not isinstance(address, str) or not address.startswith("/"):
            return
        verb = call.func.attr
        methods = next((literal(k.value, environment) for k in call.keywords if k.arg == "methods"), [verb.upper()])
        address = prefixes.get(ast.unparse(call.func.value), "") + address
        response = next((ast.unparse(k.value) for k in call.keywords if k.arg == "response_model"), "")
        function = functions.get(handler.rsplit(".", 1)[-1])
        parameters = ast.unparse(function.args) if function else "见注册函数"
        returns = ast.unparse(function.returns) if function and function.returns else ""
        for method in methods:
            yield (str(method), address, handler, parameters, response or returns or "见实现", path, line)

    def walk(node, environment):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in VERBS:
                    yield from emit(decorator, node.name, decorator.lineno, environment)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, (ast.Tuple, ast.List)):
            for value in node.iter.elts:
                for child in node.body:
                    yield from walk(child, {**environment, node.target.id: literal(value, environment)})
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_api_route":
            handler = ast.unparse(node.args[1]) if len(node.args) > 1 else "动态注册"
            yield from emit(node, handler, node.lineno, environment)
        for child in ast.iter_child_nodes(node):
            yield from walk(child, environment)

    yield from walk(tree, {})


def cell(value):
    return str(value).replace("|", "&#124;").replace("\n", " ")


def service(path):
    name = path.relative_to(ROOT).as_posix()
    if name.startswith("PlatESG/"):
        return "Plat · 8765"
    if name.startswith("database/"):
        return "数据库 · 18280"
    if name.startswith("ExtractESG/extraction/"):
        return "条款与提取 · 18180"
    return "文档与Console组合 · 18080"


def main():
    output = ROOT / "docs/reference"
    output.mkdir(parents=True, exist_ok=True)
    inventory = defaultdict(list)
    routes = defaultdict(list)
    for path in sorted(sources()):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text()) if path.suffix == ".py" else None
        title = ((ast.get_docstring(tree) or "").splitlines() or [""])[0] if tree else ""
        if not title:
            title = "测试/样例" if "/tests/" in relative or ".test." in relative else "源码/配置；职责见模块设计"
        group = "/".join(Path(relative).parts[:2]) if relative.startswith("ExtractESG/") else Path(relative).parts[0]
        inventory[group].append(f"| [{cell(relative)}](../../{relative}) | {cell(title)} |")
        if tree and "/tests/" not in relative:
            for record in route_records(path, tree):
                routes[service(path)].append(record)

    text = ["# 源码索引", "", "由`ExtractESG/.local/python/bin/python tools/build_documentation.py`生成；编辑职责说明请修改[模块设计](../03-modules.md)，不要手改本索引。", "",
            "只读取第一方源码，排除CrawlESG、私有运行数据、依赖与构建目录。单条JSON资产由ClauseESG自己的目录索引管理；生成Schema和TypeScript类型见合同规范，不在这里复制。", ""]
    for group, rows in sorted(inventory.items()):
        text += [f"## {group}", "", "| 文件 | 源码说明 |", "|---|---|", *rows, ""]
    (output / "source-map.md").write_text("\n".join(text))

    text = ["# HTTP接口参考", "", "由`ExtractESG/.local/python/bin/python tools/build_documentation.py`从当前注册源码生成；接口设计规则见[合同规范](../04-contracts.md)。", "",
            "列出装饰器、显式add_api_route及字面量循环注册，合并APIRouter前缀。参数保留Python注解和默认值；模型字段以所链接源码及服务运行时`/openapi.json`为准。这里是源码声明，不能当作服务已启动或接口已实测。", "",
            "Console静态服务的同源代理`/document`、`/extraction`、`/database`见[前端设计](../07-frontends.md)；它们不属于下面的FastAPI业务端口。Plat的旧路径410响应仍列出，用于明确退役边界。", ""]
    count = 0
    for group, records in sorted(routes.items()):
        text += [f"## {group}", "", "| 方法与路径 | 参数（含请求模型） | 返回模型 | 实现 |", "|---|---|---|---|"]
        for method, address, handler, parameters, returns, path, line in sorted(set(records), key=lambda row: (row[1], row[0])):
            relative = path.relative_to(ROOT).as_posix()
            text.append(f"| `{method} {address}` | `{cell(parameters)}` | `{cell(returns)}` | [{cell(handler)}](../../{relative}#L{line}) |")
            count += 1
        text.append("")
    (output / "http-api.md").write_text("\n".join(text))
    print(f"Generated {sum(map(len, inventory.values()))} source entries and {count} HTTP operations in docs/reference/")


if __name__ == "__main__":
    main()

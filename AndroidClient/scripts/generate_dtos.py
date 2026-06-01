#!/usr/bin/env python3
"""
Generates Kotlin data classes from JSON Schema files in shared/schemas/.

Usage (run from repo root):
    python AndroidClient/scripts/generate_dtos.py

Output:
    AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMA_DIR = REPO_ROOT / "shared" / "schemas"
OUTPUT_FILE = (
    REPO_ROOT
    / "AndroidClient"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "memebrowser"
    / "app"
    / "data"
    / "model"
    / "Models.kt"
)

PACKAGE = "com.memebrowser.app.data.model"
SKIP = {"all.schema.json"}

# Fields typed as Int rather than Float despite JSON Schema "number"
INTEGER_FIELDS = {"id", "limit", "offset", "page"}


def ref_to_class_name(ref: str, schemas_by_id: dict[str, dict]) -> str:
    filename = Path(ref.lstrip("./")).name
    schema = schemas_by_id.get(filename, {})
    return schema.get("title") or filename.replace(".schema.json", "").capitalize()


def resolve_kotlin_type(
    field_name: str,
    prop: dict,
    required: set[str],
    schemas_by_id: dict[str, dict],
) -> tuple[str, str]:
    """Return (kotlin_type, default_suffix) e.g. ('String?', ' = null')."""
    is_required = field_name in required

    if "$ref" in prop:
        cls = ref_to_class_name(prop["$ref"], schemas_by_id)
        return (cls, "") if is_required else (f"{cls}?", " = null")

    json_type = prop.get("type", "string")

    if json_type == "string":
        kt = "String"
    elif json_type == "number":
        kt = "Int" if field_name in INTEGER_FIELDS else "Float"
    elif json_type == "boolean":
        kt = "Boolean"
    elif json_type == "array":
        items = prop.get("items", {})
        if "$ref" in items:
            inner = ref_to_class_name(items["$ref"], schemas_by_id)
        else:
            inner_type = items.get("type", "Any")
            inner = "String" if inner_type == "string" else "Any"
        kt = f"List<{inner}>"
    elif json_type == "object":
        kt = "Map<String, Any>"
    else:
        kt = "Any"

    return (kt, "") if is_required else (f"{kt}?", " = null")


def generate_data_class(schema: dict, schemas_by_id: dict[str, dict]) -> str:
    title = schema["title"]
    properties: dict = schema.get("properties", {})
    required: set[str] = set(schema.get("required", []))

    lines = ["@Serializable", f"data class {title}("]
    prop_list = list(properties.items())
    for i, (name, prop_def) in enumerate(prop_list):
        kt_type, default = resolve_kotlin_type(name, prop_def, required, schemas_by_id)
        trailing = "," if i < len(prop_list) - 1 else ""
        lines.append(f'    @SerialName("{name}") val {name}: {kt_type}{default}{trailing}')
    lines.append(")")
    return "\n".join(lines)


def dependency_count(schema: dict) -> int:
    """Count how many $refs a schema has, for topological-ish ordering."""
    n = 0
    for prop in schema.get("properties", {}).values():
        if "$ref" in prop:
            n += 1
        if "$ref" in prop.get("items", {}):
            n += 1
    return n


def main() -> None:
    schemas_by_id: dict[str, dict] = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        if path.name in SKIP:
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas_by_id[path.name] = schema

    # Order: leaf types first, composite types last
    ordered = sorted(
        (s for s in schemas_by_id.values() if s.get("type") == "object" and "properties" in s),
        key=dependency_count,
    )

    header = (
        "// GENERATED — do not edit by hand.\n"
        "// Source: shared/schemas/*.schema.json\n"
        "// Regenerate: python AndroidClient/scripts/generate_dtos.py\n"
        "\n"
        f"package {PACKAGE}\n"
        "\n"
        "import kotlinx.serialization.SerialName\n"
        "import kotlinx.serialization.Serializable\n"
    )

    classes = [generate_data_class(s, schemas_by_id) for s in ordered]

    # HealthResponse is an API utility type not represented in shared schemas
    extra = (
        "\n\n"
        "// Not in shared schemas — internal API utility type\n"
        "@Serializable\n"
        "data class HealthResponse(\n"
        '    @SerialName("status") val status: String\n'
        ")\n"
    )

    output = header + "\n\n".join([""] + classes) + extra
    OUTPUT_FILE.write_text(output, encoding="utf-8")

    print(f"Generated {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    for s in ordered:
        print(f"  {s['title']}")
    print("  HealthResponse  (extra)")


if __name__ == "__main__":
    main()
datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel



or pyproject.toml :

# pyproject.toml
[tool.datamodel-code-generator]
input = "../shared/schemas/"
input_file_type = "jsonschema"
output = "app/types/generated/models.py"
target_python_version = "3.11"
output_model_type = "pydantic_v2.BaseModel"
use_standard_collections = true
use_schema_description = true
use_field_description = true
use_default_kwarg = true
use_subclass_enum = true
strict_nullable = true

datamodel-codegen --config pyproject.toml
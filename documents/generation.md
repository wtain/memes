### Frontend

```commandline
npx json2ts --cwd ../shared/schemas/ --unreachableDefinitions ../shared/schemas/all.schema.json -o ./memes-frontend/src/types/generated/all.d.ts
```

### backend

```commandline
datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
```
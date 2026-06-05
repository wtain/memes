# Environments

## Building environment

### Create .env.[environment-name] and fill:

```dotenv
DATABASE_URL=postgresql+asyncpg://...
BASE_PATH=...
VITE_BACKEND_API_URL=http://...
FRONTEND_ORIGIN=...
ALTERNATIVE_FRONTEND_ORIGIN=...
RULES_FILE=data/rules.[environment-name].json
TEXT_CONCEPTS_FILE=data/text-concepts.[environment-name].json
TEXT_CONCEPTS_TEMPLATES_FILE=data/text-concepts.templates.[environment-name].json
CONCEPT_IMAGES_DIR=images-[environment-name]
```

### Build and run database

TODO: put instructions (check SETUP.md)

### Run migrations

TODO: put instructions (check SETUP.md)
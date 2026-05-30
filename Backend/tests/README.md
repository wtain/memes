# Backend Tests

This directory contains tests for the Backend API endpoints.

## Setup

Install test dependencies:

```bash
pip install pytest pytest-asyncio pytest-mock httpx
```

## Running Tests

Run all tests:
```bash
# From the Backend directory
pytest

# Or from the project root
pytest Backend/tests/
```

Run specific test file:
```bash
pytest Backend/tests/test_images_endpoints.py
```

Run specific test class:
```bash
pytest Backend/tests/test_images_endpoints.py::TestGetImages
```

Run specific test:
```bash
pytest Backend/tests/test_images_endpoints.py::TestGetImages::test_get_images_without_query
```

Run tests with verbose output:
```bash
pytest -v
```

Run tests with coverage:
```bash
pip install pytest-cov
pytest --cov=Backend/app --cov-report=html
```

## Test Structure

- `test_images_endpoints.py` - Tests for image-related API endpoints
  - `TestGetImages` - Tests for GET /api/images
  - `TestMarkExcluded` - Tests for PUT /api/images/meme/{image_id}/mark_excluded
  - `TestUnmarkExcluded` - Tests for PUT /api/images/meme/{image_id}/unmark_excluded
  - `TestMarkUnmarkExcludedWorkflow` - Integration tests for exclude/unexclude workflow

## Test Coverage

Current coverage:
- ✅ GET /api/images (search with query, facets, pagination)
- ✅ PUT /api/images/meme/{image_id}/mark_excluded
- ✅ PUT /api/images/meme/{image_id}/unmark_excluded

## Mocking Strategy

Tests use mocked database and services:
- `mock_image_service` fixture provides a mocked `ImageService`
- Database dependency is overridden using FastAPI's dependency injection
- Tests verify correct service method calls and response formats

## Adding New Tests

1. Create a new test file in `Backend/tests/` with the prefix `test_`
2. Use the existing fixtures from `conftest.py`
3. Follow the AAA pattern (Arrange, Act, Assert)
4. Add descriptive docstrings to test functions
5. Group related tests in test classes

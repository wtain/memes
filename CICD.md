# CI/CD Pipeline Documentation

This document describes the Continuous Integration and Continuous Deployment (CI/CD) pipeline for the Memes project.

## Table of Contents

1. [Overview](#overview)
2. [GitHub Actions Workflows](#github-actions-workflows)
3. [Backend Pipeline](#backend-pipeline)
4. [Releasing](#releasing)
5. [Monitoring](#monitoring)

---

## Overview

The project uses GitHub Actions for automated testing, code quality checks, Docker image building, and releases. All workflows are configured to trigger automatically on code changes.

### Key Features

- ✅ Automated testing on Python 3.10 and 3.11
- ✅ Code coverage tracking and reporting
- ✅ Automated Docker image building and publishing
- ✅ Security scanning with Trivy
- ✅ Automated release management with semantic versioning
- ✅ Artifact preservation for auditing

---

## GitHub Actions Workflows

All workflows are located in `.github/workflows/` directory.

### Workflow Files

| File | Purpose | Trigger |
|------|---------|---------|
| `backend-tests.yml` | Run pytest suite | Push/PR to main/develop |
| `backend-coverage.yml` | Code coverage analysis | Push/PR to main/develop |
| `backend-docker.yml` | Build Docker images | Push/PR/Release |
| `release.yml` | Create GitHub releases | Git tags (v*) |

---

## Backend Pipeline

### 1. Testing Workflow (`backend-tests.yml`)

**Triggers**:
- Push to `main` or `develop`
- Pull requests to `main` or `develop`
- Changes to Backend, Storage, or requirements files

**Steps**:
1. Checkout code
2. Set up Python (3.10, 3.11 in parallel)
3. Install dependencies
4. Run flake8 linting (non-blocking)
5. Run pytest test suite
6. Generate and upload test results

**Artifacts**:
- `test-results-*.xml` - JUnit format test reports (30-day retention)

**Example Output**:
```
✓ 42 image endpoint tests
✓ 32 concept endpoint tests
✓ 10 main app tests (health, config)
✓ 84 tests total (~4 seconds)
```

**Status Badge**:
```markdown
![Backend Tests](https://github.com/YOUR_REPO/actions/workflows/backend-tests.yml/badge.svg)
```

---

### 2. Code Coverage Workflow (`backend-coverage.yml`)

**Triggers**:
- Push to `main` or `develop`
- Pull requests to `main` or `develop`

**Steps**:
1. Checkout code
2. Set up Python 3.11
3. Install dependencies + coverage tools
4. Run pytest with coverage
5. Upload to Codecov
6. Comment on PR with coverage report
7. Upload HTML coverage report

**Outputs**:
- HTML coverage report (artifact)
- Codecov integration (if token configured)
- PR comment with coverage stats

**Requirements**:
- Optional: Codecov token (set `CODECOV_TOKEN` secret)

**Coverage Targets**:
- Green threshold: ≥80%
- Orange threshold: ≥50%

---

### 3. Docker Build Workflow (`backend-docker.yml`)

**Triggers**:
- Push to `main` or `develop`
- Pull requests (build only, no push)
- GitHub releases
- Manual changes to Dockerfile.backend

**Steps**:
1. Checkout code
2. Set up Docker Buildx
3. Login to GitHub Container Registry
4. Extract metadata (version, tags)
5. Build multi-stage Docker image
6. Push to registry (if not PR)
7. Security scan with Trivy
8. Upload scan results

**Output Images**:
- `ghcr.io/YOUR_ORG/memes/backend:main` (main branch)
- `ghcr.io/YOUR_ORG/memes/backend:develop` (develop branch)
- `ghcr.io/YOUR_ORG/memes/backend:v1.0.0` (release tag)
- `ghcr.io/YOUR_ORG/memes/backend:sha-XXXXXXX` (commit SHA)

**Usage**:
```bash
# Pull latest from main
docker pull ghcr.io/YOUR_ORG/memes/backend:main

# Run container
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://... \
  ghcr.io/YOUR_ORG/memes/backend:main
```

**Requirements**:
- Docker buildx support
- GitHub Container Registry access (automatic with GITHUB_TOKEN)

---

### 4. Release Workflow (`release.yml`)

**Triggers**:
- Push of tag matching `v*` or `backend-v*`

**Steps**:
1. Wait for tests to pass
2. Wait for Docker build to complete
3. Extract version from tag
4. Generate changelog from commits
5. Create GitHub release with:
   - Version tag
   - Commit changelog
   - Docker image references
   - Release notes

**Tag Format**:
```
v1.0.0              # Full release (entire project)
backend-v1.2.3      # Backend-specific release
v2.0.0-alpha.1      # Pre-release (marked as draft)
v2.0.0-beta.2       # Beta release
```

**Example Release**:
```
Release v1.0.0

## Changes
- feat: add comprehensive test coverage
- docs: improve documentation
- fix: backend validation issues

## Docker Image
docker pull ghcr.io/YOUR_ORG/memes/backend:v1.0.0
```

---

## Releasing

### Manual Release Process

1. **Ensure main branch is ready**:
   ```bash
   git checkout main
   git pull origin main
   ```

2. **Create and push release tag**:
   ```bash
   # Full project release
   git tag -a v1.0.0 -m "Release v1.0.0"
   git push origin v1.0.0

   # Or backend-specific
   git tag -a backend-v1.2.3 -m "Backend release v1.2.3"
   git push origin backend-v1.2.3
   ```

3. **Workflow automatically**:
   - Runs tests
   - Builds Docker image
   - Creates GitHub release
   - Publishes Docker to registry

4. **Verify release**:
   - Check GitHub Releases page
   - Verify Docker image in Container Registry
   - Check workflow completion status

### Semantic Versioning

Follow [Semantic Versioning](https://semver.org/):
- `MAJOR.MINOR.PATCH`
- `v1.0.0` - Major release (breaking changes)
- `v1.1.0` - Minor release (new features)
- `v1.0.1` - Patch release (bug fixes)

### Pre-releases

For alpha/beta releases:
```bash
git tag -a v2.0.0-alpha.1 -m "Alpha release"
git push origin v2.0.0-alpha.1
```

These are automatically marked as pre-releases in GitHub.

---

## Monitoring

### Checking Workflow Status

**GitHub UI**:
1. Go to repository → Actions tab
2. View all workflows
3. Click workflow to see details
4. View logs, artifacts, and status

**Command Line**:
```bash
# View recent workflow runs
gh run list --workflow=backend-tests.yml

# View specific run
gh run view <RUN_ID>

# Download artifacts
gh run download <RUN_ID> --name test-results-3.11
```

### Success Criteria

**Tests Pass**:
- All 84 tests passing
- No Python syntax errors
- Linting warnings noted (non-blocking)

**Coverage Good**:
- Overall coverage ≥60%
- New code coverage ≥80%
- No significant drops

**Docker Build Success**:
- Image builds successfully
- Passes Trivy security scan
- Image size reasonable

---

## Troubleshooting

### Tests Failing

1. **Check the test output**:
   - View workflow logs in GitHub Actions
   - Look for assertion failures or exceptions

2. **Run locally**:
   ```bash
   cd Backend
   pytest tests/ -v
   ```

3. **Check Python version**:
   - Tests run on 3.10 and 3.11
   - Ensure local version matches

### Docker Build Failing

1. **Check Dockerfile syntax**:
   ```bash
   docker build -f Dockerfile.backend .
   ```

2. **Verify requirements.txt**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Check for new dependencies**:
   - If tests pass but Docker fails, check requirements.txt

### Coverage Drop

1. **Review coverage report**:
   - Download HTML report from artifacts
   - Identify uncovered lines

2. **Add tests for new code**:
   ```bash
   cd Backend
   pytest --cov=app --cov-report=html
   ```

### Release Not Creating

1. **Verify tag format**:
   - Must match `v*` or `backend-v*`
   - Example: `v1.0.0` or `backend-v1.2.3`

2. **Check tests passed**:
   - Release workflow waits for tests
   - View release workflow logs

3. **Verify GitHub Token**:
   - GITHUB_TOKEN should be available automatically
   - Check repository settings if issues

---

## Best Practices

### Commit Messages

Write clear commit messages for better changelogs:
```
feat: add new feature
fix: resolve bug
docs: update documentation
test: add/improve tests
refactor: code reorganization
perf: performance improvements
```

### Branch Strategy

- `main` - Production-ready code
- `develop` - Integration branch
- Feature branches - Individual features

### Testing

Always ensure tests pass before pushing:
```bash
cd Backend
pytest tests/ -v
```

### Deployment

1. Test on `develop` branch
2. Merge to `main` via PR
3. Create release tag
4. Deploy Docker image

---

## Configuration

### Required Secrets

None required for basic setup. Optional:

- `CODECOV_TOKEN` - For Codecov integration (optional)

### Environment Variables

**Backend Container**:
```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
FRONTEND_ORIGIN=http://localhost:5173
ALTERNATIVE_FRONTEND_ORIGIN=http://192.168.1.x:5173
```

See [SETUP.md](./SETUP.md#environment-configuration) for complete list.

---

## Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Docker Multi-Stage Builds](https://docs.docker.com/build/building/multi-stage/)
- [Trivy Security Scanner](https://github.com/aquasecurity/trivy)
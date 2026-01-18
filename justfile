set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# List available tasks
list:
  just --list

# Backend tasks
backend-test:
  echo "Backend tests not configured yet"

# Frontend tasks
frontend-dev:
  echo "Frontend dev server not configured yet"

# Infra tasks
infra-lint:
  echo "Infra linting not configured yet"

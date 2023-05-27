#! /bin/bash

status=0

echo "black:"
if ! black . --check; then
  status=1
fi
echo -e "---------\n"

echo "ruff:"
if ! ruff .; then
  status=1
fi
echo -e "---------\n"

echo "mypy:"
if ! mypy .; then
  status=1
fi

exit $status

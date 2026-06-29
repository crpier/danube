"""Tests for the job Resource Ceiling clamping rule (`danube.domain.limits`).

Covers the issue's acceptance criteria for each resource dimension: a request
within the ceiling is honored, a request above it is clamped to `max`, and an
absent request falls back to the server `default`.
"""

from pydantic import ValidationError
from snektest import assert_eq, assert_raises, test

from danube.domain.limits import (
    DEFAULT_CEILING,
    ResourceCeiling,
    ResourceRequest,
)

_CEILING = ResourceCeiling(
    default=ResourceRequest(cpu=1.0, memory_mb=512, pids=128, timeout_seconds=600),
    max=ResourceRequest(cpu=4.0, memory_mb=4096, pids=1024, timeout_seconds=3600),
)


@test(mark="fast")
def test_request_within_ceiling_is_honored() -> None:
    resolved = _CEILING.resolve(
        ResourceRequest(cpu=2.0, memory_mb=2048, pids=256, timeout_seconds=1800)
    )
    assert_eq(resolved.cpu, 2.0)
    assert_eq(resolved.memory_mb, 2048)
    assert_eq(resolved.pids, 256)
    assert_eq(resolved.timeout_seconds, 1800)


@test(mark="fast")
def test_request_above_ceiling_is_clamped_to_max() -> None:
    resolved = _CEILING.resolve(
        ResourceRequest(cpu=16.0, memory_mb=65536, pids=99999, timeout_seconds=999999)
    )
    assert_eq(resolved.cpu, 4.0)
    assert_eq(resolved.memory_mb, 4096)
    assert_eq(resolved.pids, 1024)
    assert_eq(resolved.timeout_seconds, 3600)


@test(mark="fast")
def test_absent_request_falls_back_to_default() -> None:
    resolved = _CEILING.resolve(ResourceRequest())
    assert_eq(resolved.cpu, 1.0)
    assert_eq(resolved.memory_mb, 512)
    assert_eq(resolved.pids, 128)
    assert_eq(resolved.timeout_seconds, 600)


@test(mark="fast")
def test_partial_request_mixes_honored_and_default() -> None:
    # Only CPU is requested; the other dimensions fall back to the default.
    resolved = _CEILING.resolve(ResourceRequest(cpu=3.0))
    assert_eq(resolved.cpu, 3.0)
    assert_eq(resolved.memory_mb, 512)
    assert_eq(resolved.pids, 128)
    assert_eq(resolved.timeout_seconds, 600)


@test(mark="fast")
def test_unset_max_imposes_no_cap() -> None:
    # `max` unset (the built-in default ceiling) never clamps a request down.
    ceiling = ResourceCeiling(default=ResourceRequest(cpu=2.0))
    resolved = ceiling.resolve(ResourceRequest(cpu=64.0, memory_mb=1, pids=1))
    assert_eq(resolved.cpu, 64.0)
    assert_eq(resolved.memory_mb, 1)
    assert_eq(resolved.pids, 1)


@test(mark="fast")
def test_unset_field_everywhere_stays_none() -> None:
    resolved = ResourceCeiling().resolve(ResourceRequest())
    assert_eq(resolved.cpu, None)
    assert_eq(resolved.memory_mb, None)
    assert_eq(resolved.pids, None)
    assert_eq(resolved.timeout_seconds, None)


@test(mark="fast")
def test_default_ceiling_matches_historical_caps() -> None:
    # A job that requests nothing is limited exactly as before this feature.
    resolved = DEFAULT_CEILING.resolve(ResourceRequest())
    assert_eq(resolved.cpu, 2.0)
    assert_eq(resolved.memory_mb, 2048)
    assert_eq(resolved.pids, 512)
    assert_eq(resolved.timeout_seconds, 3600)


@test(mark="fast")
def test_from_table_absent_returns_default_ceiling() -> None:
    assert_eq(ResourceCeiling.from_table(None), DEFAULT_CEILING)
    assert_eq(ResourceCeiling.from_table({}), DEFAULT_CEILING)


@test(mark="fast")
def test_from_table_merges_over_default_ceiling() -> None:
    # Only `max` is configured; the built-in `default` must survive the merge.
    ceiling = ResourceCeiling.from_table({"max": {"cpu": 8.0, "memory_mb": 16384}})
    assert_eq(ceiling.default, DEFAULT_CEILING.default)
    assert_eq(ceiling.max.cpu, 8.0)
    assert_eq(ceiling.max.memory_mb, 16384)
    # Unspecified max fields stay unbounded.
    assert_eq(ceiling.max.pids, None)
    assert_eq(ceiling.max.timeout_seconds, None)


@test(mark="fast")
def test_from_table_partial_default_keeps_other_built_in_fields() -> None:
    ceiling = ResourceCeiling.from_table({"default": {"cpu": 1.0}})
    assert_eq(ceiling.default.cpu, 1.0)
    assert_eq(ceiling.default.memory_mb, DEFAULT_CEILING.default.memory_mb)
    assert_eq(ceiling.default.pids, DEFAULT_CEILING.default.pids)


@test(mark="fast")
def test_from_table_rejects_unknown_key() -> None:
    with assert_raises(ValidationError):
        _ = ResourceCeiling.from_table({"max": {"cpus": 8}})


@test(mark="fast")
def test_non_positive_values_are_rejected() -> None:
    with assert_raises(ValidationError):
        _ = ResourceRequest(cpu=0)
    with assert_raises(ValidationError):
        _ = ResourceRequest(memory_mb=-1)


@test(mark="fast")
def test_unknown_field_is_rejected() -> None:
    with assert_raises(ValidationError):
        _ = ResourceRequest.model_validate({"cpus": 2})

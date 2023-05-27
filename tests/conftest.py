import pytest

MARKERS = {
    "unit": "test code with no IO",
    "component": " target only one individual component",
    "integration": " target multiple components",
    "acceptance": " target whole workflow when interfacing with the application",
}


def pytest_configure(config: pytest.Config) -> None:
    for marker_name, description in MARKERS.items():
        config.addinivalue_line("markers", f"{marker_name}: {description}")


def pytest_itemcollected(item: pytest.Function) -> None:
    for marker in item.own_markers:
        if marker.name in MARKERS:
            break
    else:
        markers_list = " ".join([marker.name for marker in item.own_markers])
        msg = (
            f"Test {item} doesn't have any of the custom markers, "
            f"instead it has: {markers_list}"
        )
        raise ValueError(msg)

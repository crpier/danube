from snektest import assert_true, test

import danube


@test(mark="fast")
def test_import_danube_exposes_version() -> None:
    assert_true(len(danube.__version__) > 0)

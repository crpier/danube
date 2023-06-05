"""
A simple dependency injection framework.

Decorate functions that need to be injected with `@injectable` and
annotate the parameters that need to be injected with `Injected`.

Note: Injected parameters must be keyword-only.

Example:
```python
@injectable
def do_print(
    foo: str = "default",
    *,
    bar: Annotated[str, Injected],
) -> None:
    print(foo)
    print(bar)
```
Running `do_print()` print
```
default
weird
```
"""
from collections.abc import Callable
from functools import wraps
from inspect import _ParameterKind, get_annotations, signature
from typing import Annotated, Any, get_args, get_origin


class MissingDependencyError(Exception):
    ...


class IncorrectInjectableSignatureError(Exception):
    ...


class DoubleInjectionError(Exception):
    ...


_INJECTS: dict[str, Any] = {}


class Injected:
    ...


def injectable(func: Callable):  # noqa: ANN201
    @wraps(func)
    def wrapper(*args, **kwargs):  # noqa: [ANN002, ANN003]
        for name, annotation in get_annotations(func).items():
            if get_origin(annotation) is Annotated and any(
                meta is Injected for meta in get_args(annotation)
            ):
                lol = signature(func).parameters[name]
                if lol.kind is not _ParameterKind.KEYWORD_ONLY:
                    msg = (
                        f"Injectable parameter {name} in "
                        f"{func.__name__} must be keyword-only"
                    )
                    raise IncorrectInjectableSignatureError(msg)
                if name in _INJECTS:
                    kwargs.update({name: _INJECTS[name]})
                else:
                    msg = f"Missing dependency for {name} in {func.__name__}"
                    raise MissingDependencyError(msg)
        return func(*args, **kwargs)

    return wrapper


def add_injectable(name: str, injectable: Any) -> None:  # noqa: ANN401
    if name in _INJECTS:
        msg = f"Injectable {name} already exists"
        raise DoubleInjectionError(msg)
    _INJECTS[name] = injectable

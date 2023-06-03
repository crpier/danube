import typing as t
from typing import Generic, TypeVar

import click
import pydantic
from click import Argument, Context, Option
from click.core import Parameter
from click.decorators import FC
from pydantic.error_wrappers import ErrorList, ErrorWrapper
from pydantic.fields import ModelField

T = TypeVar("T", covariant=True)


class PydanticSchemaType(click.ParamType, Generic[T]):
    def __init__(self, schema: type[pydantic.BaseModel], field: ModelField) -> None:
        self.schema = schema
        self.model_field = field
        self.name = field.name

    def convert(
        self,
        value: str | None,
        param: Parameter | None,
        ctx: Context | None,
    ) -> T:
        if param and param.name:
            if isinstance(value, Sentinel):
                value = self.model_field.default
                if value is None:
                    self.fail(
                        "Missing required parameter",
                        param,
                        ctx,
                    )
            res: tuple[T | None, ErrorList | None] = self.schema.__fields__[
                param.name
            ].validate(
                value,
                {},
                loc=param.name,
            )
            new_value, error = res
            if new_value is None:
                self.fail(
                    f"Invalid value for {param.name}: {value}",
                    param,
                    ctx,
                )
            if error:
                if isinstance(error, ErrorWrapper):
                    self.fail(str(error.exc), param, ctx)
                else:
                    self.fail(f"Unhandleable errors: {error}", param, ctx)
            return new_value  # type: ignore

        msg = "Now idea how we got here"
        raise NotImplementedError(msg)


class Sentinel:
    pass


# TODO: find a way to re-decorate functions that use `argument`, instead of this
def pyargument(*param_decls: str, **attrs: t.Any) -> t.Callable[[FC], FC]:
    attrs["type"] = PydanticSchemaType(
        attrs["schema"],
        attrs["schema"].__fields__[param_decls[0].lower().replace("-", "_")],
    )
    attrs["default"] = (
        attrs["schema"].__fields__[param_decls[0].lower().replace("-", "_")].default
        or Sentinel
    )
    del attrs["schema"]

    def decorator(f: FC) -> FC:
        ArgumentClass = attrs.pop("cls", None) or Argument
        click.decorators._param_memo(f, ArgumentClass(param_decls, **attrs))
        return f

    return decorator


# TODO: find a way to re-decorate functions that use `option`, instead of this
def pyoption(*param_decls: str, **attrs: t.Any) -> t.Callable[[FC], FC]:
    attrs["type"] = PydanticSchemaType(
        attrs["schema"],
        attrs["schema"].__fields__[
            param_decls[0].lower().lstrip("-").replace("-", "_")
        ],
    )
    attrs["default"] = (
        attrs["schema"]
        .__fields__[param_decls[0].lower().lstrip("-").replace("-", "_")]
        .default
        or Sentinel
    )
    del attrs["schema"]

    def decorator(f: FC) -> FC:
        option_attrs = attrs.copy()
        OptionClass = option_attrs.pop("cls", None) or Option
        click.decorators._param_memo(f, OptionClass(param_decls, **option_attrs))
        return f

    return decorator

import ast
import subprocess
from pathlib import Path
from textwrap import dedent

from pydantic import BaseModel


def get_pipeline_config(tree, file_content):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.value.id == "PIPELINE"
                    and target.attr == "config"
                ):
                    return ast.get_source_segment(
                        file_content,
                        node.value,
                    )


class PipelineConfig(BaseModel):
    image: str | Path
    auto_clone_checkout: bool = True


def parse_config(raw_config: str) -> PipelineConfig:
    raw_config = raw_config.lstrip("{")
    raw_config = raw_config.strip("\n")
    raw_config = raw_config.rstrip("}")
    raw_config = raw_config.strip("\n")
    raw_config = dedent(raw_config)
    data = {}
    for line in raw_config.split("\n"):
        line = line.rstrip(",")
        key, value = line.split(":", 1)
        key = key.strip('"')
        value = value.strip()
        match value:
            case "True":
                value = True
            case "False":
                value = False
            case _:
                value = value.strip('"')
        data[key] = value
    return PipelineConfig.model_validate(data)


def load_pipeline_config(danubefile: Path):
    with open(danubefile, "r") as file:
        file_content = file.read()
    tree = ast.parse(file_content)
    pipeline_config = get_pipeline_config(tree, file_content)

    result = subprocess.run(
        ["ruff", "format", "--isolated", "--config", "line-length=1", "-"],
        input=pipeline_config,
        text=True,
        capture_output=True,
    )
    pipeline_config = result.stdout
    return parse_config(pipeline_config)


config = load_pipeline_config(Path("./example_danubefile.py"))
print(config)

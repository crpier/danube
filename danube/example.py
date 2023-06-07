import datetime
from pathlib import Path

from danube.pipeline_config import Ops, Pipeline

DOCKER_IMAGES = {"ubuntu": "ubuntu:latest", "danube-dev": Path("./Dockerfile")}

pipeline = Pipeline(DOCKER_IMAGES=DOCKER_IMAGES, CONFIG={"max_time": datetime.timedelta(hours=1)})
ops = Ops(pipeline)

@pipeline.stage(on=DOCKER_IMAGES["ubuntu"])
def init_workspace(pipeline: Pipeline, ops: Ops):
    stdout, stderr = ops.shell("echo Hello World!")
    print(stdout, stderr)

    @init_workspace.stage
    def part_1_init(pipeline: Pipeline, ops: Ops):
        stdout, stderr = ops.shell("echo I am in stage 1!")
        print(stdout, stderr)
        if stderr != "":
            pipeline.set_stage_status("Failed")

    @init_workspace.stage(on=DOCKER_IMAGES["danube-dev"])
    def part_2_init(pipeline: Pipeline, ops: Ops):
        stdout, stderr = ops.shell("echo I am in stage 2!")
        print(stdout, stderr)
        if stdout != "":
            msg = "Do you want to deploy I guess?"
            if pipeline.prompt_confirmation(msg):
                return
        pipeline.stop()


@init_workspace.stage()
def part_3_init(pipeline: Pipeline, ops: Ops):
    stdout, stderr = ops.shell("echo I am in stage 2!")
    print(stdout, stderr)

# or, alternatively:
# pipeline.register_stages({init_workspace: [part_1_init, part_2_init, part_3_init]})

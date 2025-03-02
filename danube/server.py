import os
from typing import Dict, List

import docker
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()

# Initialize Docker client
docker_client = docker.from_env()

# In-memory storage for logs and job information
logs = []
jobs = {}


class LogMessage(BaseModel):
    message: str


class ContainerRequest(BaseModel):
    image: str
    command: str


class ContainerResponse(BaseModel):
    container_id: str


@app.post("/log", status_code=201)
async def add_log(log_message: LogMessage):
    logs.append(log_message.message)
    return {"status": "Log added successfully"}


@app.get("/logs", response_model=List[str])
async def get_logs():
    return logs


@app.post("/container", response_model=ContainerResponse)
async def create_container(container_request: ContainerRequest):
    try:
        container = docker_client.containers.run(
            container_request.image, command=container_request.command, detach=True
        )
        return ContainerResponse(container_id=container.id)
    except docker.errors.DockerException as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/container/{container_id}/logs")
async def get_container_logs(container_id: str):
    try:
        container = docker_client.containers.get(container_id)

        async def log_generator():
            for line in container.logs(stream=True, follow=True):
                yield line.decode()

        return StreamingResponse(log_generator(), media_type="text/plain")
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    except docker.errors.DockerException as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/branch/is_default")
async def is_default_branch():
    # This is a placeholder. In a real-world scenario, you'd implement
    # logic to determine if the current execution is on the default branch.
    # This might involve checking environment variables or integrating
    # with a version control system.
    return {"is_default": True}


@app.post("/job/start")
async def start_job(job_info: Dict):
    job_id = len(jobs) + 1
    jobs[job_id] = {"status": "running", "info": job_info}
    return {"job_id": job_id}


@app.post("/job/{job_id}/complete")
async def complete_job(job_id: int):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    jobs[job_id]["status"] = "completed"
    return {"status": "Job marked as completed"}


if __name__ == "__main__":
    host = os.environ.get("DANUBE_HOST", "0.0.0.0")
    port = int(os.environ.get("DANUBE_PORT", 8000))
    uvicorn.run(app, host=host, port=port)

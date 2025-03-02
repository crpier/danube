import importlib.util
import os
from pathlib import Path
from typing import Any, Callable, Dict

import docker
import requests



class DanubeRunner:
    def __init__(self, danube_server_url: str):
        self.client = docker.from_env()
        self.danube_server_url = danube_server_url
        self.container_ids = []

    
    def create_main_container(self, danubefile_path: str, image: str):
        self.main_container = self.client.containers.run(
            image,
            command="tail -f /dev/null",  # Keep the container running
            volumes={
                os.path.abspath(os.path.dirname(danubefile_path)): {"bind": "/app", "mode": "ro"},
            },
            detach=True,
            environment={
                "DANUBE_SERVER_URL": self.danube_server_url,
            },
        )
        self.container_ids.append(self.main_container.id)
        return self.main_container


    
    def run_danubefile(self, danubefile_path: str):
        # Load the danubefile to extract the image information
        danubefile = load_danubefile(danubefile_path)
        pipeline_decorator = getattr(danubefile.run, '__wrapped__', danubefile.run)
        image = pipeline_decorator.__closure__[0].cell_contents

        # Build the image if it's a Path, otherwise use it as is
        if isinstance(image, Path):
            image = self.build_image(image)

        # Create the main container with the correct image
        self.create_main_container(danubefile_path, image)
        
        # Execute the danubefile in the main container
        exit_code, output = self.main_container.exec_run("python /app/danubefile.py")
        
        # Stream logs from the container in real-time
        for line in output.decode().split('\n'):
            print(line.strip(), flush=True)

        if exit_code != 0:
            raise Exception(f"Danubefile execution failed with status code {exit_code}")


    def api_call(
        self, endpoint: str, method: str = "GET", data: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        url = f"{self.danube_server_url}/{endpoint}"
        response = requests.request(method, url, json=data)
        response.raise_for_status()
        return response.json()

    def create_stage_container(self, image: str | None, command: str | Callable, name: str) -> str:
        if callable(command):
            # If it's a Python function, run it directly in the main container
            result = command()
            # Log the result
            self.api_call("log", method="POST", data={"message": f"[{name}] {str(result)}"})
            return "function_executed"  # Return a dummy ID for consistency
        elif image is None:
            # If no image is specified, run the command in the main container
            exit_code, output = self.main_container.exec_run(command)
            for line in output.decode().split('\n'):
                self.api_call("log", method="POST", data={"message": f"[{name}] {line.strip()}"})
            if exit_code != 0:
                raise Exception(f"Command execution failed with status code {exit_code}")
            return "main_container_executed"
        else:
            # For string commands with a specified image, use Docker as before
            container = self.client.containers.run(image, command=command, detach=True, remove=False, name=name)
            if container.id is None:
                raise Exception("Failed to create container")
            self.container_ids.append(container.id)
            return container.id

    def get_container_logs(self, container_id: str):
        url = f"{self.danube_server_url}/container/{container_id}/logs"
        with requests.get(url, stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    yield line.decode()
        
    def remove_container(self, container_id: str):
        if container_id != "function_executed":
            try:
                container = self.client.containers.get(container_id)
                container.remove(force=True)
                self.container_ids.remove(container_id)
            except docker.errors.NotFound:
                # Container already removed or doesn't exist
                pass
            except ValueError:
                # Container ID not in the list
                pass

    def remove_all_containers(self):
        for container_id in self.container_ids[:]:
            self.remove_container(container_id)
        self.container_ids.clear()

    def build_image(self, dockerfile_path: Path) -> str:
        image, _ = self.client.images.build(
            path=str(dockerfile_path.parent),
            dockerfile=dockerfile_path.name,
            rm=True
        )
        return image.id


class DanubeAPI:
    def __init__(self, runner) -> None:
        self.runner = runner
        self.current_stage = None

    def log(self, message: str):
        formatted_message = f"[{self.current_stage}] {message}" if self.current_stage else message
        self.runner.api_call("log", method="POST", data={"message": formatted_message})

    def run_command(self, image: str | None, command: str, name: str) -> str:
        # I don't think it's a good idea to not spanw a new container if it's the same image
        if image is None or image == self.runner.main_container.image.tags[0]:
            # Run in the main container
            exit_code, output = self.runner.main_container.exec_run(command)
            for line in output.decode().split('\n'):
                self.log(f"[{name}] {line.strip()}")
            if exit_code != 0:
                raise Exception(f"Command execution failed with status code {exit_code}")
            return "main_container_executed"
        else:
            # Create a new container
            return self.runner.create_stage_container(image, command, name)

    def get_logs(self, container_id: str, name: str):
        if container_id == "main_container_executed":
            # Logs are already handled for main container executions
            return
        for line in self.runner.get_container_logs(container_id):
            formatted_line = f"[{name}] {line.strip()}"
            print(formatted_line, flush=True)
            self.log(formatted_line)

    def set_current_stage(self, stage_name: str):
        self.current_stage = stage_name

    def is_default_branch(self) -> bool:
        return True

    def build_image(self, dockerfile_path: Path) -> str:
        image_id = self.runner.build_image(dockerfile_path)
        self.log(f"Built image: {image_id}")
        return image_id

    def remove_container(self, container_id: str):
        self.runner.remove_container(container_id)


def load_danubefile(filepath: str):
    spec = importlib.util.spec_from_file_location("danubefile.py", filepath)
    if spec is None:
        raise ImportError(f"Could not find danubefile.py at {filepath}")
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(
            f"Could not load danubefile.py at {filepath} with spec {spec}"
        )
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    danube_server_url = os.environ.get("DANUBE_SERVER_URL", "http://localhost:8000")
    runner = DanubeRunner(danube_server_url)

    danubefile_path = "danubefile.py"  # Update this path if necessary

    try:
        # Run the danubefile using the run_danubefile method
        runner.run_danubefile(danubefile_path)
        print("Danubefile execution completed successfully")
    finally:
        # Remove all containers after the pipeline has finished
        runner.remove_all_containers()

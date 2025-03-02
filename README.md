# Danube

Things I'm learning:
- So, it looks like I can't have the `if __name__ == "__main__":` in the file, because when using `runpy.run_path` it will try to run the file as a module (the `__name__` is `<run_path>`)


Thank you for providing the actual content of your `danubefile.py`. Now that I can see the exact implementation, I can provide a more accurate analysis and suggestions.

Let's evaluate the current format and consider potential improvements:

1. Current Format Evaluation:

The current format of `danubefile.py` is clean, Pythonic, and relatively easy to read. It leverages Python's decorators and context managers to create a domain-specific language (DSL) for defining pipelines. This approach has several advantages:

- It's familiar to Python developers.
- It allows for easy integration of Python code directly in the pipeline.
- It's concise and expressive.

However, there are some potential challenges this format might face when implementing more complex Jenkins-like functionality:

a) Limited Structure: The current format doesn't explicitly define stages as separate entities, which might make it harder to implement more complex stage behaviors or relationships.

b) Error Handling: There's no explicit error handling or pipeline recovery mechanisms visible in this format.

c) Parallel Execution: Jenkins allows for parallel execution of stages, which isn't immediately apparent how to implement in this format.

d) Conditional Execution: While there is a simple conditional for the "push" stage, more complex conditionals might become unwieldy in this format.

e) Reusability: The current format doesn't show how to define reusable steps or stages that could be shared across multiple pipelines.

2. Suggestions for Improvements:

a) Explicit Stage Definitions: Consider defining stages as separate objects or functions that can be composed. This could make it easier to add more complex behaviors to stages.

```python
def linting_stage(pipeline):
    with Stage("linting", image="ruff:latest") as s:
        s.run("ruff check .")
        s.run("ruff format --check .")

@pipeline(image="dind:latest", auto_clone_checkout=True)
def run(pipeline: Pipeline):
    linting_stage(pipeline)
    # Other stages...
```

b) Error Handling: Implement a way to handle and recover from errors in stages.

```python
try:
    with Stage("testing") as s:
        s.run(lambda: pytest.main(["-v", "--color=yes"]))
except StageError as e:
    pipeline.handle_error(e)
```

c) Parallel Execution: Introduce a mechanism for parallel execution of stages.

```python
with Parallel() as p:
    p.add(Stage("test_unit"))
    p.add(Stage("test_integration"))
```

d) More Flexible Conditionals: Implement a more robust conditional system.

```python
@condition(lambda p: p.on_default_branch and p.is_release)
def push_stage(pipeline):
    with Stage("push", image="dind:latest") as s:
        s.run("docker build -t danube-py3-none-any .")

@pipeline(image="dind:latest", auto_clone_checkout=True)
def run(pipeline: Pipeline):
    # Other stages...
    push_stage(pipeline)
```

e) Parameterization: Allow for easy parameterization of pipelines.

```python
@pipeline(image="dind:latest", auto_clone_checkout=True)
def run(pipeline: Pipeline, version: str = "latest"):
    with Stage("build") as s:
        s.run(f"uv build --version {version}")
```

f) Pipeline Visualization: Consider how you might represent the pipeline structure in a way that can be easily visualized or exported to a graph.

g) Extensibility: Design the system so that users can easily add custom Stage types or Pipeline behaviors.

```python
class CustomStage(Stage):
    def __init__(self, name, custom_param):
        super().__init__(name)
        self.custom_param = custom_param

    def run(self):
        # Custom implementation

with CustomStage("my_custom_stage", custom_param="value") as s:
    s.run()
```

These suggestions aim to make your system more flexible and capable of handling complex CI/CD scenarios while maintaining the Pythonic and readable nature of your current implementation. The key is to design an extensible system that can grow to accommodate more features as you implement them, without losing the simplicity and clarity of the current format.


# Things we've learned
1. Summary of the Architecture:

The current prototype of Danube consists of several key components:

a) Server (danube/server.py):
   - A FastAPI application that handles HTTP requests.
   - Manages logs, containers, and job information.
   - Interfaces with Docker for container operations.

b) Runner (danube/runner.py):
   - Responsible for executing the Danubefile.
   - Creates and manages Docker containers.
   - Communicates with the Danube server.

c) Danube Library (lib/danube.py):
   - Defines the core abstractions: Pipeline and Stage.
   - Provides decorators and context managers for defining pipelines.

d) Danubefile (danubefile.py):
   - User-defined pipeline using the Danube library.
   - Specifies stages and commands to be executed.

The architecture follows a client-server model where the Runner acts as a client to both the Docker daemon and the Danube server. The Danube library provides a high-level API for users to define their pipelines, which are then interpreted and executed by the Runner.

2. List of Requirements:

Based on the current implementation, here are the key requirements to keep in mind:

a) Docker Integration:
   - Ability to create, manage, and remove Docker containers.
   - Support for building Docker images from Dockerfiles.

b) Pipeline Definition:
   - Allow users to define pipelines using a Python-based DSL.
   - Support for defining stages with custom images and commands.

c) Execution Environment:
   - Provide a consistent execution environment for pipeline stages.
   - Support for running commands in the main container or separate containers.

d) Logging and Monitoring:
   - Comprehensive logging of all pipeline activities.
   - Real-time streaming of container logs.

e) Branch Awareness:
   - Ability to determine if the pipeline is running on the default branch.

f) Flexibility:
   - Support for both shell commands and Python callable in stages.

g) Error Handling:
   - Proper error handling and reporting throughout the pipeline execution.

h) Resource Management:
   - Efficient creation and cleanup of Docker resources.

i) API Design:
   - Clean and intuitive API for users to define and run pipelines.

j) Configurability:
   - Allow users to configure various aspects of the pipeline execution.

3. List of Tests:

Here's a list of tests to consider for test-driven development:

Unit Tests:

a) Pipeline Definition:
   - Test pipeline decorator with various parameters.
   - Test Stage creation with different options.

b) Docker Operations:
   - Test image building from Dockerfile.
   - Test container creation with different parameters.

c) Logging:
   - Test log message formatting.
   - Test log storage and retrieval.

d) Command Execution:
   - Test execution of shell commands.
   - Test execution of Python callables.

e) Error Handling:
   - Test error reporting for failed commands.
   - Test error handling for Docker operations.

f) Branch Detection:
   - Test is_default_branch functionality.

Integration Tests:

a) Full Pipeline Execution:
   - Test execution of a simple pipeline with multiple stages.
   - Test pipeline with stages using different images.

b) Docker Integration:
   - Test building and using a custom image in a pipeline.
   - Test proper cleanup of Docker resources after pipeline execution.

c) Server-Runner Communication:
   - Test communication between Runner and Server components.

d) Log Streaming:
   - Test real-time log streaming from containers to the client.

e) Error Scenarios:
   - Test pipeline behavior with intentionally failing commands.
   - Test recovery and reporting of Docker-related errors.

f) Resource Limits:
   - Test behavior when system resources (e.g., memory, CPU) are constrained.

g) Concurrent Executions:
   - Test multiple pipelines running concurrently.

h) Long-Running Pipelines:
   - Test stability and resource management for long-running pipelines.

i) Network Interactions:
   - Test pipelines that involve network operations (if applicable).

j) File System Interactions:
   - Test pipelines that involve file system operations.

These tests will help ensure that the new implementation meets the requirements and maintains the functionality of the current prototype while allowing for improvements and refactoring.

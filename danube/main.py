import docker


def main():
    client = docker.from_env()
    result = client.api.pull(
        "hello-world",
        tag="latest",
        stream=True,
        decode=True,
    )
    for line in result:
        print(line)


if __name__ == "__main__":
    main()

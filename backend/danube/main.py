from anyio import run

import danube.k8s.client


async def main():
    client = danube.k8s.client.K8sClient()
    pod = await client.create_pod("my-namespace", "test-pod")
    print(pod)
    _ = input("Press enter to delete pod")
    await client.delete_pod(pod)


if __name__ == "__main__":
    run(main)

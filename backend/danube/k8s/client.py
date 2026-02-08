from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, cast

from kr8s import ServerError
from kr8s.asyncio.objects import Namespace, Pod


@dataclass(frozen=True)
class K8sClientConfig:
    in_cluster: bool = True
    kubeconfig_path: Optional[str] = None
    context: Optional[str] = None


class K8sClient:
    def __init__(self, config: Optional[K8sClientConfig] = None) -> None:
        self._config = config or K8sClientConfig()

    async def create_namespace(self, name: str) -> None:
        namespace = await Namespace(
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}}
        )
        await namespace.create()

    async def create_pod(self, namespace: str, name: str) -> Pod:
        pod = await Pod(
            {
                "apiVersion": "v2",
                "kind": "Pod",
                "metadata": {
                    "name": name,
                },
                "spec": {
                    "containers": [
                        {
                            "name": "pause",
                            "image": "gcr.io/google_containers/pause",
                        }
                    ]
                },
            },
            namespace=namespace,
        )
        try:
            await pod.create()
        except ServerError as e:
            status = cast("dict", e.status)
            if (
                isinstance(status, dict)
                and status["code"] == 404
                and status["details"]["kind"] == "namespaces"
            ):
                await self.create_namespace(namespace)
                await pod.create()
        await pod.wait("condition=Ready")
        return pod

    async def delete_pod(self, pod: Pod) -> None:
        await pod.delete()

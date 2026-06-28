"""HTTP/JSON RPC endpoints the Coordinator SDK calls on the Master.

Each route validates the job session via the control plane and maps control-plane
errors to HTTP status codes:

- unknown / closed job session -> 404
- bad bearer token             -> 401
- unauthorized secret          -> 403
- illegal status transition    -> 409
"""

from fastapi import APIRouter, HTTPException, status

from danube.domain.lifecycle import InvalidTransition
from danube.rpc.control_plane import (
    ArtifactSourceError,
    InvalidTokenError,
    SecretNotAuthorizedError,
    SessionNotActiveError,
)
from danube.rpc.deps import ControlPlaneDep, TokenDep
from danube.rpc.schemas import (
    GetSecretRequest,
    GetSecretResponse,
    ReportStatusRequest,
    ReportStatusResponse,
    RunStepRequest,
    RunStepResponse,
    UploadArtifactRequest,
    UploadArtifactResponse,
)

router = APIRouter(prefix="/rpc", tags=["rpc"])


def _reject_session(error: SessionNotActiveError | InvalidTokenError) -> HTTPException:
    if isinstance(error, InvalidTokenError):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
        )
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


@router.post("/run-step")
async def run_step(
    request: RunStepRequest, token: TokenDep, control_plane: ControlPlaneDep
) -> RunStepResponse:
    try:
        return await control_plane.run_step(request.job_id, token, request)
    except (SessionNotActiveError, InvalidTokenError) as error:
        raise _reject_session(error) from None


@router.post("/get-secret")
async def get_secret(
    request: GetSecretRequest, token: TokenDep, control_plane: ControlPlaneDep
) -> GetSecretResponse:
    try:
        value = await control_plane.get_secret(request.job_id, token, request.key)
    except (SessionNotActiveError, InvalidTokenError) as error:
        raise _reject_session(error) from None
    except SecretNotAuthorizedError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(error)
        ) from None
    return GetSecretResponse(key=request.key, value=value)


@router.post("/upload-artifact")
async def upload_artifact(
    request: UploadArtifactRequest, token: TokenDep, control_plane: ControlPlaneDep
) -> UploadArtifactResponse:
    try:
        return await control_plane.upload_artifact(
            request.job_id,
            token,
            request.name,
            request.path,
        )
    except (SessionNotActiveError, InvalidTokenError) as error:
        raise _reject_session(error) from None
    except ArtifactSourceError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from None


@router.post("/report-status")
async def report_status(
    request: ReportStatusRequest, token: TokenDep, control_plane: ControlPlaneDep
) -> ReportStatusResponse:
    try:
        return await control_plane.report_status(
            request.job_id, token, request.state, request.detail
        )
    except (SessionNotActiveError, InvalidTokenError) as error:
        raise _reject_session(error) from None
    except InvalidTransition as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from None

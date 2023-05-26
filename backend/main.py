from fastapi import APIRouter, FastAPI

app = FastAPI()
api_router = APIRouter()


@api_router.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")

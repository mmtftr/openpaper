from fastapi import APIRouter
from pydantic import BaseModel

# Create API router with prefix
router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    message: str


@router.get("/health")
def health_check() -> HealthResponse:
    """
    Health check endpoint to verify the API is running
    """
    return HealthResponse(status="healthy", message="Service is running")

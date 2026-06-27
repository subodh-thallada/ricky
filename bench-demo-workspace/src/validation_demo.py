def validate_student_payload(payload: dict[str, object]) -> dict[str, object]:
    """Bench demo: several valid validation styles exist for the same payload."""
from fastapi import FastAPI, Header, HTTPException, status

def create_app() -> FastAPI:
    app = FastAPI()
    
    @app.get("/health")
    def health():
        return {"status": "ok"}
    
    @app.get("/protected")
    def protected(authorization: str = Header(None)):
        if authorization is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header"
            )
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format"
            )
        token = authorization.split(" ")[1]
        if token != "test-token":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid token"
            )
        return {"message": "Access granted", "token": token}
    
    return app

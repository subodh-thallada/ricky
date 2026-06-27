from fastapi import Depends, FastAPI, Header, HTTPException


def require_token(authorization: str | None = Header(default=None)) -> None:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if authorization != "Bearer test-token":
        raise HTTPException(status_code=403, detail="Invalid bearer token")


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/protected", dependencies=[Depends(require_token)])
    def protected():
        return {"authenticated": True, "strategy": "dependency"}

    return app

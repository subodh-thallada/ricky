from fastapi import FastAPI, Header, HTTPException


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/protected")
    def protected(authorization: str | None = Header(default=None)):
        if authorization is None:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        if authorization != "Bearer test-token":
            raise HTTPException(status_code=403, detail="Invalid bearer token")
        return {"authenticated": True, "strategy": "inline"}

    return app

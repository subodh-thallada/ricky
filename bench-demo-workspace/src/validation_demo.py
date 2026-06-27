def validate_student_payload(payload: dict[str, object]) -> dict[str, object]:
    """Bench demo: several valid validation styles exist for the same payload."""
from fastapi import FastAPI, Request, HTTPException


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/protected":
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            
            if not auth_header:
                response = HTTPException(status_code=401, detail="Missing bearer token")
                await response(scope, receive, send)
                return
            
            if auth_header != "Bearer test-token":
                response = HTTPException(status_code=403, detail="Invalid bearer token")
                await response(scope, receive, send)
                return
        
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    app = FastAPI()
    app = AuthMiddleware(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/protected")
    def protected():
        return {"authenticated": True, "strategy": "middleware"}

    return app

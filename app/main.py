from fastapi import FastAPI

from app.api.v1.auth import router as auth_router

app = FastAPI(title="FastAPI", description="API description goes here", version="0.1.0")

app.include_router(auth_router, prefix="/api/v1")


@app.get("/")
def root():
    return {"message": "Hello World!"}


@app.get("/health")
def health():
    return {"status": "ok"}

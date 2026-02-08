from fastapi import FastAPI

app = FastAPI(
    title="FastAPI",
    description="API description goes here",
    version="0.1.0"
)

@app.get("/")
def root():
    return {"message": "Hello World!"}

@app.get("/health")
def health():
    return {"status": "ok"}

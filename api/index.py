from fastapi import FastAPI
import db   # 👈 이거만 추가

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

from fastapi import FastAPI
import db   

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

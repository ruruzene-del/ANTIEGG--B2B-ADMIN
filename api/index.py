from fastapi import FastAPI
import db

db.init_db()   

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

from fastapi import FastAPI
import db   

app = FastAPI()

@app.get("/")
@app.get("/favicon.png")
def favicon():
    return Response(status_code=204)
def health():
    return {"status": "ok"}



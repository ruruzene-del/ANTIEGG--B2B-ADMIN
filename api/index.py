from backend.supabase_client import supabase
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/ping")
def ping():
    return {"pong": True}


@app.get("/deals/test")
def deals_test():
    return {
        "data": [
            {
                "id": 1,
                "company": "ANTIEGG",
                "stage": "REVIEWING",
                "contact": "류진",
                "email": "test@antiegg.com"
            },
            {
                "id": 2,
                "company": "TEST",
                "stage": "SIGNED",
                "contact": "홍길동",
                "email": "test2@antiegg.com"
            }
        ]
    }
@app.get("/deals/db")
def get_deals_db():
    res = supabase.table("deals").select("*").execute()
    return {"data": res.data}


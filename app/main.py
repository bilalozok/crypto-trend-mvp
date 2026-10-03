from fastapi import FastAPI

app = FastAPI(title="crypto-trend-mvp")


@app.get("/")
async def root():
    return {"ok": True, "service": "crypto-trend-mvp"}


@app.get("/health")
async def health():
    return {"ok": True, "service": "crypto-trend-mvp"}

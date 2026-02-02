import uvicorn

if __name__ == "__main__":
    # reload=True for local dev only; Docker CMD runs uvicorn directly without reload
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

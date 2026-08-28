"""
Application runner for Alpha Whales Intelligence.
Run: python run.py
"""
import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print(" Alpha Whales Intelligence")
    print(" Tracking 26 Elite Value, Quality & Concentrated Managers")
    print(" Serving at: http://localhost:8000")
    print("=" * 60)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

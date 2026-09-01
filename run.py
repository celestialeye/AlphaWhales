"""
Application runner for Alpha Whales Intelligence.
Run: python run.py
"""
import uvicorn
from config import FUND_MANAGERS

if __name__ == "__main__":
    print("=" * 60)
    print(" Alpha Whales Intelligence")
    print(f" Tracking {len(FUND_MANAGERS)} Selected and Exception Managers")
    print(" Serving at: http://localhost:8000")
    print("=" * 60)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

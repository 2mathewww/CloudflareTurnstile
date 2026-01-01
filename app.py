#!/usr/bin/env python3

import json
import asyncio
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from utils.turnstile import TurnstileSolver

CONFIG_PATH = "data/config.json"

def load_config():
    """Load configuration from JSON file"""
    default_config = {
        "headless": True,
        "thread": 2,
        "browser_type": "chromium",
        "proxy_support": False,
        "api": {
            "enabled": False,
            "host": "0.0.0.0",
            "port": 8000
        }
    }
    
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
            print(f"✅ Loaded config from {CONFIG_PATH}")
            
            merged = {**default_config, **config}
            if 'api' in config:
                merged['api'] = {**default_config['api'], **config['api']}
            
            return merged
    except FileNotFoundError:
        print(f"⚠️  Config file {CONFIG_PATH} not found, using defaults")
        return default_config
    except Exception as e:
        print(f"❌ Error loading config: {str(e)}, using defaults")
        return default_config

config = load_config()

print("\n" + "="*50)
print("CURRENT CONFIGURATION")
print("="*50)
print(json.dumps(config, indent=2))
print("="*50 + "\n")

solver = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global solver
    try:
        solver = TurnstileSolver(
            headless=config['headless'],
            thread=config['thread'],
            browser_type=config['browser_type'],
            proxy_support=config['proxy_support']
        )
        await solver.initialize()
        print("✅ Turnstile Solver initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize solver: {str(e)}")
        raise
    
    yield
    
    if solver:
        await solver.cleanup()
        print("✅ Turnstile Solver cleaned up")

app = FastAPI(
    title="Cloudflare Turnstile Solver API",
    description="API untuk menyelesaikan Cloudflare Turnstile Challenge",
    version="1.0.0",
    lifespan=lifespan
)

class TurnstileRequest(BaseModel):
    url: str
    sitekey: str
    action: Optional[str] = None
    cdata: Optional[str] = None

class SolverStatus(BaseModel):
    initialized: bool
    thread_count: int
    browser_type: str
    headless: bool
    has_display: bool
    user_agent: str
    pool_size: int

@app.get("/")
async def root():
    return {
        "name": "Cloudflare Turnstile Solver API",
        "version": "1.0.0",
        "config": config,
        "endpoints": {
            "/": "API info",
            "/status": "Solver status",
            "/api/solve": "Solve Turnstile (GET & POST)",
            "/health": "Health check"
        }
    }

@app.get("/status")
async def get_status():
    if not solver:
        raise HTTPException(status_code=503, detail="Solver not initialized")
    
    status = solver.get_status()
    return SolverStatus(**status)

@app.get("/api/solve")
async def solve_get(
    url: str = Query(..., description="Target website URL"),
    sitekey: str = Query(..., description="Cloudflare Turnstile sitekey"),
    action: Optional[str] = Query(None, description="Optional action parameter"),
    cdata: Optional[str] = Query(None, description="Optional cdata parameter")
):
    if not solver:
        raise HTTPException(status_code=503, detail="Solver not initialized")
    
    try:
        result = await solver.solve(url, sitekey, action, cdata)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/solve")
async def solve_post(request: TurnstileRequest):
    if not solver:
        raise HTTPException(status_code=503, detail="Solver not initialized")
    
    try:
        result = await solver.solve(
            request.url, 
            request.sitekey, 
            request.action, 
            request.cdata
        )
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    if not solver:
        return {"status": "down", "message": "Solver not initialized"}
    
    status = solver.get_status()
    if status["initialized"]:
        return {"status": "up", "pool_size": status["pool_size"]}
    else:
        return {"status": "down", "message": "Solver pool empty"}

async def run_cli():
    solver_instance = TurnstileSolver(
        headless=config['headless'],
        thread=config['thread'],
        browser_type=config['browser_type'],
        proxy_support=config['proxy_support']
    )
    
    try:
        await solver_instance.initialize()
        print("\nSolver Status:", json.dumps(solver_instance.get_status(), indent=2))
        
        print("\nExample usage:")
        print("1. await solver.solve('https://example.com', '0x4AAAAAAAB...')")
        print("2. await solver.solve('https://example.com', '0x4AAAAAAAB...', action='login', cdata='user123')")
        
        print("\nPress Ctrl+C to exit...")
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        await solver_instance.cleanup()

def main():
    if config['api']['enabled']:
        print(f"🚀 Starting API server on {config['api']['host']}:{config['api']['port']}")
        uvicorn.run(
            app,
            host=config['api']['host'],
            port=config['api']['port'],
            reload=False,
            log_level="info"
        )
    else:
        print("🚀 Starting in CLI mode...")
        asyncio.run(run_cli())

if __name__ == "__main__":
    main()
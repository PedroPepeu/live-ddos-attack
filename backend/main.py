import os
import json
import asyncio
from typing import Optional, List, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from pydantic import BaseModel, Field
import asyncpg
import redis.asyncio as redis
from contextlib import asynccontextmanager

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Global connection pools
db_pool = None
redis_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client
    # Startup
    # Retry loop for DB connection
    for i in range(10):
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
            break
        except (OSError, asyncpg.CannotConnectNowError) as e:
            print(f"Database not ready, retrying in 2s... ({e})")
            await asyncio.sleep(2)
    else:
        raise Exception("Could not connect to database after retries")

    redis_client = await redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    
    # Start Redis listener background task
    task = asyncio.create_task(start_redis_listener())
    
    yield
    
    # Shutdown
    if db_pool:
        await db_pool.close()
    if redis_client:
        await redis_client.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Attack Map API", lifespan=lifespan)

# Pydantic model for ingest
class AttackIn(BaseModel):
    ts: Optional[str] = None  # ISO timestamp optional
    src_ip: str
    src_asn: Optional[int] = None
    src_country: Optional[str] = None
    src_lat: Optional[float] = None
    src_lon: Optional[float] = None
    dst_ip: Optional[str] = None
    dst_port: Optional[int] = None
    attack_type: Optional[str] = None
    score: Optional[float] = None
    meta: Optional[dict] = Field(default_factory=dict)

async def insert_attack(conn, attack: AttackIn):
    query = """
    INSERT INTO attacks (ts, src_ip, src_asn, src_country, src_lat, src_lon,
                         dst_ip, dst_port, attack_type, score, meta, geom)
    VALUES (
        coalesce($1, now()),
        $2, $3, $4, $5, $6,
        $7, $8, $9, $10, $11,
        CASE
          WHEN $5 IS NOT NULL AND $6 IS NOT NULL THEN ST_SetSRID(ST_MakePoint($6, $5), 4326)::geography
          ELSE NULL
        END
    )
    RETURNING id, ts;
    """
    # Convert ts string to datetime if present, else None (let DB use now())
    ts_val = None
    if attack.ts:
        # Simple ISO parsing or let asyncpg handle it if it's a datetime object
        # For simplicity, passing as is if asyncpg handles string->timestamptz, 
        # otherwise we might need datetime.fromisoformat(attack.ts)
        # asyncpg usually expects datetime objects for TIMESTAMPTZ
        from datetime import datetime
        try:
            ts_val = datetime.fromisoformat(attack.ts.replace('Z', '+00:00'))
        except ValueError:
            pass 

    record = await conn.fetchrow(query,
                                 ts_val,
                                 attack.src_ip,
                                 attack.src_asn,
                                 attack.src_country,
                                 attack.src_lat,
                                 attack.src_lon,
                                 attack.dst_ip,
                                 attack.dst_port,
                                 attack.attack_type,
                                 attack.score,
                                 json.dumps(attack.meta))
    return record

@app.post("/ingest")
async def ingest(attack: AttackIn, background_tasks: BackgroundTasks):
    async with db_pool.acquire() as conn:
        rec = await insert_attack(conn, attack)

    # Publish to Redis
    payload = {
        "id": rec["id"],
        "ts": rec["ts"].isoformat(),
        "src_ip": attack.src_ip,
        "src_lat": attack.src_lat,
        "src_lon": attack.src_lon,
        "attack_type": attack.attack_type,
        "score": attack.score,
        "src_country": attack.src_country,
        "dst_ip": attack.dst_ip,
        "dst_port": attack.dst_port
    }
    
    background_tasks.add_task(redis_client.publish, "attacks", json.dumps(payload))

    return {"status": "ok", "id": rec["id"]}

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: str):
        to_remove = []
        for conn in list(self.active_connections):
            try:
                await conn.send_text(message)
            except Exception:
                to_remove.append(conn)
        for r in to_remove:
            self.active_connections.discard(r)

manager = ConnectionManager()

@app.websocket("/ws/attacks")
async def ws_attacks(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # Keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)

async def start_redis_listener():
    """Background task to subscribe to Redis and broadcast to WebSockets"""
    # Create a dedicated connection for subscription
    pubsub_client = await redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    async with pubsub_client.pubsub() as pubsub:
        await pubsub.subscribe("attacks")
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    data = message["data"]
                    await manager.broadcast(data)
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub_client.close()

@app.get("/")
async def root():
    return {"message": "DDoS Attack Map API is running"}
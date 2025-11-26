import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://localhost:8001/ws/attacks"
    async with websockets.connect(uri) as websocket:
        print("Connected to WebSocket")
        
        # Keep listening for messages
        try:
            while True:
                message = await websocket.recv()
                print(f"Received: {message}")
                data = json.loads(message)
                if data.get("src_ip") == "1.2.3.4":
                    print("Test Passed: Received expected attack data")
                    break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_websocket())

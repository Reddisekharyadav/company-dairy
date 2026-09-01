import asyncio
import websockets
import json

async def run():
    async with websockets.connect('ws://127.0.0.1:60459') as ws:
        # Send a CDP command to evaluate javascript
        req = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "document.body.innerText"
            }
        }
        await ws.send(json.dumps(req))
        resp = await ws.recv()
        data = json.loads(resp)
        if 'result' in data and 'result' in data['result']:
            text = data['result']['result'].get('value', 'No value')
            # Write it to a file
            with open('github_error.txt', 'w', encoding='utf-8') as f:
                f.write(text)
            print("Successfully extracted text")
        else:
            print("Failed to evaluate:", data)

asyncio.run(run())

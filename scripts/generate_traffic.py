import asyncio
import httpx
import time
import random

ENDPOINT = "http://localhost:8080/predict"
PROMPTS = [
    "I need help with a billing issue.",
    "My app is crashing with error 500.",
    "I want to update my account email.",
    "Why was I charged twice?",
    "Search the knowledge base for 'latency issues'.",
]

async def send_request(client, i):
    payload = {
        "prompt": random.choice(PROMPTS),
        "session_id": f"load-test-{i}"
    }
    try:
        start = time.time()
        response = await client.post(ENDPOINT, json=payload, timeout=30.0)
        latency = time.time() - start
        return response.status_code, latency
    except Exception as e:
        return "ERROR", 0

async def main():
    print(f"🚀 Starting load test: 1000 requests to {ENDPOINT}")
    async with httpx.AsyncClient() as client:
        tasks = [send_request(client, i) for i in range(1000)]
        results = await asyncio.gather(*tasks)
    
    success_count = sum(1 for res in results if res[0] == 200)
    errors = sum(1 for res in results if res[0] == "ERROR" or (isinstance(res[0], int) and res[0] >= 400))
    avg_latency = sum(res[1] for res in results if res[1] > 0) / (success_count or 1)
    
    print("\n" + "="*30)
    print("📈 LOAD TEST SUMMARY")
    print(f"Total Requests: 1000")
    print(f"Successes:      {success_count}")
    print(f"Failures:       {errors}")
    print(f"Avg Latency:    {avg_latency:.2f}s")
    print("="*30)

if __name__ == "__main__":
    asyncio.run(main())

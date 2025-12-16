import time
import requests
import json

def generate(model, prompt):
    url = "http://localhost:11434/api/generate"
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }
    start_time = time.time()
    response = requests.post(url, json=data)
    end_time = time.time()
    
    if response.status_code == 200:
        return end_time - start_time, response.json().get("response", "").strip()
    else:
        raise Exception(f"Error: {response.text}")

def main():
    models = ["qwen2.5:14b", "deepseek-r1:32b"]
    
    print(f"Testing model swap latency between {models[0]} and {models[1]}...")
    print("-" * 50)

    # 1. Warm-up Qwen
    print(f"1. Loading {models[0]} (Warm-up)...")
    duration, _ = generate(models[0], "Hello!")
    print(f"   Took {duration:.2f}s")

    # 2. Switch to Deepseek (Swap Penalty)
    print(f"2. Switching to {models[1]} (SWAP HAPPENING)...")
    try:
        duration, response = generate(models[1], "Explain quantum physics in one sentence.")
        print(f"   Took {duration:.2f}s")
        print(f"   Response: {response[:50]}...")
    except Exception as e:
        print(f"   Failed: {e}")

    # 3. Switch back to Qwen (Swap Penalty)
    print(f"3. Switching back to {models[0]} (SWAP HAPPENING)...")
    try:
        duration, response = generate(models[0], "What is 2+2?")
        print(f"   Took {duration:.2f}s")
        print(f"   Response: {response[:50]}...")
    except Exception as e:
        print(f"   Failed: {e}")

if __name__ == "__main__":
    main()

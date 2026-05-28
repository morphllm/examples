import os
import requests

MORPH_API_KEY = os.environ["MORPH_API_KEY"]
MODEL_ID = os.environ.get("REFLEX_MODEL_ID", "your-model-id")


def classify(text: str) -> dict:
    res = requests.post(
        "https://api.morphllm.com/v1/reflex/predict",
        headers={
            "Authorization": f"Bearer {MORPH_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": MODEL_ID, "text": text},
    )
    res.raise_for_status()
    return res.json()


texts = [
    "I need a refund for my order, this is unacceptable",
    "How do I reset my password?",
    "Your product is amazing, saved me hours of work",
    "Can you add dark mode support?",
]

for text in texts:
    result = classify(text)
    label = result["label"]
    confidence = result["confidence"] * 100
    print(f'"{text}"')
    print(f"  → {label} ({confidence:.1f}%)\n")

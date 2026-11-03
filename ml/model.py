from functools import lru_cache
from pathlib import Path

import joblib

MODEL_PATH = Path(__file__).with_name("model.joblib")
CATEGORIES = ("dependency", "test", "lint/type", "build/compile", "config-secret", "infra-flaky")


@lru_cache(maxsize=1)
def load_model():
    return joblib.load(MODEL_PATH)


def predict(log: str) -> tuple[str, float]:
    model = load_model()
    probabilities = model.predict_proba([log])[0]
    index = probabilities.argmax()
    return str(model.classes_[index]), float(probabilities[index])

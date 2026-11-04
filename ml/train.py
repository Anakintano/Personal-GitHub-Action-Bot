import argparse
import csv
import sqlite3
from contextlib import closing
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline

if __package__:
    from .model import CATEGORIES, MODEL_PATH
else:
    from model import CATEGORIES, MODEL_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the synthetic seed and train the CI classifier.")
    parser.add_argument("--db", type=Path, help="Read collected analyses from an existing SQLite database")
    args = parser.parse_args()

    with Path(__file__).with_name("data").joinpath("seed.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    logs = [row["log"] for row in rows]
    labels = [row["category"] for row in rows]

    train_x, held_out_x, train_y, held_out_y = train_test_split(
        logs, labels, test_size=0.25, stratify=labels, random_state=42
    )
    model = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2)), LogisticRegression(C=10, max_iter=1000)
    )
    model.fit(train_x, train_y)
    predicted = model.predict(held_out_x)
    accuracy = accuracy_score(held_out_y, predicted)
    print(f"Synthetic held-out accuracy ({len(held_out_y)} examples): {accuracy:.1%}")
    print("Rows = actual, columns = predicted:", ", ".join(CATEGORIES))
    print(confusion_matrix(held_out_y, predicted, labels=CATEGORIES))

    if args.db:
        with closing(sqlite3.connect(f"{args.db.resolve().as_uri()}?mode=ro", uri=True)) as db:
            for log, category in db.execute("SELECT trimmed_log, category FROM analyses"):
                if log and category in CATEGORIES:
                    logs.append(log)
                    labels.append(category)

    model.fit(logs, labels)
    joblib.dump(model, MODEL_PATH)
    print(f"Saved {MODEL_PATH.name} from {len(logs)} examples.")


if __name__ == "__main__":
    main()

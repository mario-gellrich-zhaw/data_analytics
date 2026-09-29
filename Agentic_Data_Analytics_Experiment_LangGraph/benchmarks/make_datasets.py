"""Create the offline datasets in datasets/ (deterministic). Run once: python -m benchmarks.make_datasets"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.datasets import load_breast_cancer, load_diabetes

from ada.paths import datasets_dir


def synthetic_rentals(n: int = 3000, seed: int = 7) -> pd.DataFrame:
    """A fictional city ('Lakeside') with 12 districts. Rent depends non-linearly on size,
    district, distance to the centre, age, floor and amenities, plus noise and some messiness
    (duplicates, missing values, unit typos) so cleaning matters."""
    rng = np.random.default_rng(seed)
    districts = pd.DataFrame({
        "district": [f"D{i:02d}" for i in range(1, 13)],
        "d_lat": 47.37 + rng.normal(0, 0.025, 12), "d_lon": 8.54 + rng.normal(0, 0.035, 12),
        "premium": rng.uniform(0.85, 1.35, 12),
    })
    idx = rng.integers(0, 12, n)
    d = districts.iloc[idx].reset_index(drop=True)
    lat = d["d_lat"] + rng.normal(0, 0.006, n)
    lon = d["d_lon"] + rng.normal(0, 0.008, n)
    rooms = rng.choice([1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5], n, p=[.06, .06, .12, .14, .16, .16, .12, .09, .05, .04])
    area = np.clip(rooms * rng.normal(24, 4, n) + rng.normal(8, 6, n), 18, 260)
    year = rng.integers(1890, 2024, n)
    floor = rng.integers(0, 9, n)
    balcony = rng.random(n) < 0.6
    elevator = (rng.random(n) < 0.5) | (floor >= 5)
    dist = np.sqrt(((lat - 47.37) * 111) ** 2 + ((lon - 8.54) * 75) ** 2)
    age = 2024 - year
    rent = (d["premium"].to_numpy() * (14 + 22 * np.exp(-dist / 3.5)) * area ** 0.93
            + 90 * balcony + 60 * elevator + 18 * np.minimum(floor, 6)
            - 2.2 * np.clip(age, 0, 60) + 400 * (year >= 2015) + rng.normal(0, 140, n))
    rent = np.round(np.clip(rent, 450, None) / 5) * 5
    df = pd.DataFrame({
        "listing_id": [f"LS-{100000 + i}" for i in range(n)],
        "district": d["district"], "latitude": lat.round(6), "longitude": lon.round(6),
        "rooms": rooms, "living_area": area.round(1).astype(str) + " m2", "year_built": year.astype(float),
        "floor": floor, "has_balcony": np.where(balcony, "yes", "no"), "has_elevator": elevator.astype(int),
        "monthly_rent_chf": rent,
    })
    # messiness: missing values, typos, duplicates, implausible rows
    df.loc[rng.random(n) < 0.08, "year_built"] = np.nan
    df.loc[rng.random(n) < 0.05, "floor"] = -99
    df.loc[rng.random(n) < 0.03, "living_area"] = df["living_area"].str.replace(" m2", " sqm")
    bad = rng.random(n) < 0.01
    df.loc[bad, "monthly_rent_chf"] = df.loc[bad, "monthly_rent_chf"] * 1000
    dups = df.sample(60, random_state=seed)
    return pd.concat([df, dups]).sample(frac=1, random_state=seed).reset_index(drop=True)


def main() -> None:
    out = datasets_dir()
    out.mkdir(exist_ok=True)
    dia = load_diabetes(as_frame=True, scaled=False).frame.rename(columns={"target": "disease_progression"})
    dia.insert(0, "patient_id", [f"P{i:04d}" for i in range(len(dia))])
    dia.to_csv(out / "diabetes.csv", index=False)
    bc = load_breast_cancer(as_frame=True)
    frame = bc.frame.copy()
    frame["diagnosis"] = np.where(frame.pop("target") == 1, "benign", "malignant")
    frame.insert(0, "sample_id", [f"S{i:04d}" for i in range(len(frame))])
    frame.to_csv(out / "breast_cancer.csv", index=False)
    synthetic_rentals().to_csv(out / "synthetic_rentals.csv", index=False)
    print("wrote", sorted(p.name for p in out.glob("*.csv")))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Scraper for CDJM (Conseil de Déontologie Journalistique et de Médiation) avis.
Fetches the public JSON API and updates cdjm.json.
"""

import json
import re
from datetime import date
from html import unescape
from pathlib import Path

import requests

API_URL = "https://yannguegan.pythonanywhere.com/data/dzc/cdjm-data.json"
PDF_BASE = "https://cdjm.org/files/avis/"

STATUS_MAP = {
    "1- Saisine non fond": "Non fondée",
    "2- Saisine partiellement fond": "Partiellement fondée",
    "3- Saisine fond": "Fondée",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

OUTPUT_FILE = Path(__file__).parent / "cdjm.json"


def normalize_status(raw: str) -> str | None:
    for prefix, normalized in STATUS_MAP.items():
        if raw.startswith(prefix):
            return normalized
    return None


def fetch_data() -> dict:
    resp = requests.get(API_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_avis(raw_avis: list[dict]) -> list[dict]:
    result = []
    for a in raw_avis:
        status = normalize_status(a.get("status", ""))
        if not status:
            continue
        date_decision = a.get("date_decision", "").strip()
        if not date_decision:
            continue
        medium = unescape(re.sub(r"<[^>]+>", "", a.get("medium", ""))).strip()
        if not medium:
            continue
        avis_id = a.get("id", "").strip()
        result.append(
            {
                "id": avis_id,
                "date": date_decision,
                "medium": medium,
                "topic": a.get("topic", "").strip(),
                "status": status,
                "url": f"{PDF_BASE}{avis_id}.pdf",
            }
        )
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


def main():
    print("=== Scraper CDJM ===")
    data = fetch_data()
    avis = build_avis(data.get("avis", []))

    print(f"{len(avis)} avis trouvés.")
    from collections import Counter
    statuts = Counter(a["status"] for a in avis)
    for s, n in statuts.most_common():
        print(f"  {s}: {n}")

    output = {
        "last_updated": date.today().isoformat(),
        "source": API_URL,
        "avis": avis,
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Fichier mis à jour : {OUTPUT_FILE}")

    html_file = OUTPUT_FILE.parent / "index.html"
    if html_file.exists():
        html = html_file.read_text(encoding="utf-8")
        inline = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
        html_new = re.sub(
            r"<script>window\.CDJM_DATA = .*?;</script>",
            f"<script>window.CDJM_DATA = {inline};</script>",
            html,
            flags=re.DOTALL,
        )
        if html_new != html:
            html_file.write_text(html_new, encoding="utf-8")
            print(f"Données CDJM injectées dans : {html_file}")
        else:
            print("Pattern CDJM_DATA non trouvé dans index.html — pas d'injection.")


if __name__ == "__main__":
    main()

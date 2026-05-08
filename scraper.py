#!/usr/bin/env python3
"""
Scraper for Arcom decisions on the 4 French continuous news channels.
Fetches all pages from arcom.fr and updates decisions.json.
"""

import json
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.arcom.fr/se-documenter/espace-juridique/decisions"
PARAMS = {
    "field_type_de_decision_target_id[23]": "23",   # Interventions
    "field_type_de_decision_target_id[11]": "11",   # Mises en garde
    "field_type_de_decision_target_id[22]": "22",   # Mises en demeure
    "field_type_de_decision_target_id[262]": "262", # Sanctions financières
    "field_prise_en_region_value": "All",
    "sort_bef_combine": "field_date_de_decision_value_DESC",
}

# Keywords to match each channel (lowercase, checked against title)
CHANNEL_PATTERNS = {
    "BFM TV": [
        r"\bbfm\s*tv\b",
        r"\bbfmtv\b",
    ],
    "CNews": [
        r"\bcnews\b",
        r"\bc\s*news\b",
    ],
    "LCI": [
        r"\blci\b",
        r"\bla\s+cha[iî]ne\s+info\b",
    ],
    "Franceinfo": [
        r"\bfranceinfo\b",
        r"\bfrance\s*info\b",
        r"\bfranceinformation\b",
    ],
}

TYPE_MAP = {
    "sanction p[eé]cuniaire": "Sanction financière",
    "sanction financi[eè]re": "Sanction financière",
    "mise en demeure": "Mise en demeure",
    "mise en garde": "Mise en garde",
    "intervention": "Intervention",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

OUTPUT_FILE = Path(__file__).parent / "decisions.json"


def normalize_type(raw: str) -> str | None:
    raw_lower = raw.lower()
    for pattern, normalized in TYPE_MAP.items():
        if re.search(pattern, raw_lower):
            return normalized
    return None


def detect_channels(title: str) -> list[str]:
    title_lower = title.lower()
    matched = []
    for channel, patterns in CHANNEL_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, title_lower):
                matched.append(channel)
                break
    return matched


FRENCH_MONTHS = {
    "janvier": "01", "février": "02", "mars": "03", "avril": "04",
    "mai": "05", "juin": "06", "juillet": "07", "août": "08",
    "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12",
}


def parse_date(raw: str) -> str | None:
    """Convert DD/MM/YYYY, YYYY-MM-DD, or '03 mai 2026' to YYYY-MM-DD."""
    raw = raw.strip()
    try:
        if re.match(r"\d{2}/\d{2}/\d{4}", raw):
            return datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
        if re.match(r"\d{4}-\d{2}-\d{2}", raw):
            return raw
        # French long-form: "03 mai 2026"
        m = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
        if m:
            day, month_str, year = m.group(1), m.group(2).lower(), m.group(3)
            month = FRENCH_MONTHS.get(month_str)
            if month:
                return f"{year}-{month}-{int(day):02d}"
    except ValueError:
        pass
    return None


def fetch_page_info(url: str) -> tuple[str | None, list[str]]:
    """Fetch publication date and channel mentions from an individual decision page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        pub_date = None
        for tag in soup.find_all(string=re.compile(r"Publié\s+le", re.I)):
            m = re.search(r"Publié\s+le\s+(\d{1,2}\s+\w+\s+\d{4})", str(tag), re.I)
            if m:
                pub_date = parse_date(m.group(1))
                break
        channels = detect_channels(soup.get_text())
        return pub_date, channels
    except Exception:
        pass
    return None, []


def fetch_page(page: int = 0) -> BeautifulSoup:
    params = {**PARAMS, "page": page}
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_decisions(soup: BeautifulSoup) -> list[dict]:
    decisions = []

    # New ARCOM structure (2026): .views-row > .card.card-v3
    rows = soup.select("div.views-row")

    if not rows:
        # Legacy fallback
        rows = soup.select("article.node--type-decision, li.views-row, .view-content article")

    for row in rows:
        # Title + URL
        title_el = row.select_one("h2.card-title a, h3 a, h2 a, .node__title a, .field--name-title a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        url = title_el.get("href", "")
        if url and not url.startswith("http"):
            url = "https://www.arcom.fr" + url

        # Date — new structure: first <li> in .card-infos .tag-list ("04 mai 2026")
        date_el = row.select_one(
            ".card-infos ul.tag-list li, "
            ".field--name-field-date-de-decision time, time[datetime], .date-display-single"
        )
        raw_date = ""
        if date_el:
            raw_date = date_el.get("datetime") or date_el.get_text(strip=True)
        parsed_date = parse_date(raw_date)

        # Decision type — new structure: first <li> in .card-footer .tag-list
        type_el = row.select_one(
            ".card-footer ul.tag-list li, "
            ".field--name-field-type-de-decision, .field--type-entity-reference"
        )
        decision_type = None
        if type_el:
            decision_type = normalize_type(type_el.get_text(strip=True))
        if not decision_type:
            decision_type = normalize_type(title)

        if not decision_type:
            continue

        # Channels — try listing title first, fall back to full page text
        channels = detect_channels(title)
        pub_date = None
        if url:
            pub_date, page_channels = fetch_page_info(url)
            time.sleep(0.5)
            if not channels and page_channels:
                channels = page_channels
        if not channels:
            continue

        if pub_date:
            parsed_date = pub_date
        if not parsed_date:
            continue

        for channel in channels:
            decisions.append(
                {
                    "date": parsed_date,
                    "type": decision_type,
                    "channel": channel,
                    "title": title,
                    "url": url,
                }
            )

    return decisions


def get_total_pages(soup: BeautifulSoup) -> int:
    # New Bootstrap pagination (2026)
    pager = soup.select_one("ul.pagination")
    if pager:
        # Find the highest page= value among all pagination links
        max_page = 0
        for a in pager.select("a.page-link"):
            m = re.search(r"page=(\d+)", a.get("href", ""))
            if m:
                max_page = max(max_page, int(m.group(1)))
        if max_page:
            return max_page + 1
    # Legacy Drupal pager fallback
    pager = soup.select_one("nav.pager ul, .pager__items")
    if not pager:
        return 1
    last = pager.select_one("li.pager__item--last a, li:last-child a")
    if last:
        m = re.search(r"page=(\d+)", last.get("href", ""))
        if m:
            return int(m.group(1)) + 1
    return max(len(pager.select("li")) - 2, 1)


def scrape_all() -> list[dict]:
    print("Fetching page 0…")
    soup0 = fetch_page(0)
    total_pages = get_total_pages(soup0)
    print(f"  → {total_pages} page(s) detected")

    all_decisions = parse_decisions(soup0)

    for page in range(1, total_pages):
        print(f"Fetching page {page}…")
        time.sleep(1)
        soup = fetch_page(page)
        all_decisions.extend(parse_decisions(soup))

    # Deduplicate by (date, channel, title)
    seen = set()
    unique = []
    for d in all_decisions:
        key = (d["date"], d["channel"], d["title"])
        if key not in seen:
            seen.add(key)
            unique.append(d)

    # Sort by date descending, assign IDs
    unique.sort(key=lambda x: x["date"], reverse=True)
    for i, d in enumerate(unique, 1):
        d["id"] = i

    return unique


def main():
    print("=== Arcom scraper — chaînes d'info en continu ===")
    decisions = scrape_all()
    if not decisions:
        raise SystemExit("ERREUR : 0 décision trouvée — la structure du site a peut-être changé. Données non écrasées.")
    print(f"\n{len(decisions)} décision(s) trouvée(s) pour les 4 chaînes.")

    channels = {}
    for d in decisions:
        channels.setdefault(d["channel"], 0)
        channels[d["channel"]] += 1
    for ch, n in sorted(channels.items()):
        print(f"  {ch}: {n}")

    output = {
        "last_updated": date.today().isoformat(),
        "source": BASE_URL,
        "decisions": decisions,
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Fichier mis à jour : {OUTPUT_FILE}")

    # Injecte les données inline dans index.html (fonctionne sans serveur)
    html_file = OUTPUT_FILE.parent / "index.html"
    if html_file.exists():
        import re as _re
        html = html_file.read_text(encoding="utf-8")
        inline = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
        html = _re.sub(
            r"<script>window\.ARCOM_DATA = .*?;</script>",
            f"<script>window.ARCOM_DATA = {inline};</script>",
            html,
            flags=_re.DOTALL,
        )
        html_file.write_text(html, encoding="utf-8")
        print(f"Données injectées dans : {html_file}")


if __name__ == "__main__":
    main()

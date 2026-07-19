from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re
import json
import datetime as dt
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0 Safari/537.36"
)


@dataclass
class PlayerLinkSnapshot:
    navn: str
    alder: Optional[float]
    vaerdi_mio: Optional[float]
    rating: Optional[float]
    position_code: str
    engine_position: str
    url: str
    stats: dict[str, float] | None = None
    training_xp_average: Optional[float] = None
    training_xp_total: Optional[float] = None
    training_xp_count: int = 0
    training_url: Optional[str] = None
    training_parse_warning: Optional[str] = None
    avatar_url: Optional[str] = None
    avatar_image_bytes: Optional[bytes] = None
    avatar_content_type: Optional[str] = None


def extract_player_id(url: str) -> str:
    raw = str(url or "").strip()
    if re.fullmatch(r"\d+", raw):
        return raw

    m = re.search(r"/players/(\d+)", raw)
    if not m:
        raise ValueError("Kunne ikke udtrække spiller-id. Indtast fx 17374538 eller et VMAN-spillerlink.")
    return m.group(1)


def _clean_player_url(reference: str) -> str:
    raw = str(reference or "").strip()
    if not raw:
        raise ValueError("Indtast et Spillerlink.")

    # Accepter links uden scheme, fx:
    # www.virtualmanager.com/da/players/123-navn
    # virtualmanager.com/players/123-navn
    # .virtualmanager.com/players/123-navn  (kan ske hvis feltet er vandret scrollet)
    if raw.startswith(".virtualmanager.com"):
        raw = "https://www" + raw
    elif raw.startswith("www."):
        raw = "https://" + raw
    elif raw.startswith("virtualmanager.com"):
        raw = "https://" + raw

    if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
        return raw

    parts = urlsplit(raw)
    path = re.sub(r"/training/?$", "", parts.path.rstrip("/"), flags=re.IGNORECASE)
    return urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))


def vman_player_slug(name: str) -> str:
    """Create the slug VMAN uses in player profile URLs.

    VMAN removes non-ASCII characters completely instead of transliterating
    them. Examples: Millán -> milln, Militão -> milito, Darío -> daro,
    Bård -> brd.
    """
    slug = str(name or "").lower().strip()
    slug = re.sub(r"[\s_/]+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return re.sub(r"-+", "-", slug).strip("-")


def build_vman_player_url(player_id: int | str, player_name: str, language: str | None = None) -> str:
    player_id = str(player_id).strip()
    slug = vman_player_slug(player_name)
    suffix = f"{player_id}-{slug}" if slug else player_id
    prefix = f"/{language.strip('/')}" if language else ""
    return f"https://www.virtualmanager.com{prefix}/players/{suffix}"


def player_url_candidates(reference: str, player_name: str | None = None) -> list[str]:
    """Return robust player profile candidates in priority order.

    1. Use the actual VMAN link supplied by the page/user.
    2. Try a reconstructed link using VMAN's special slug rule when a name is
       available.
    3. Fall back to id-only profile links.
    """
    raw = str(reference or "").strip()
    player_id = extract_player_id(raw)
    candidates: list[str] = []

    if not re.fullmatch(r"\d+", raw):
        cleaned = _clean_player_url(raw)
        if re.match(r"^https?://", cleaned, flags=re.IGNORECASE):
            candidates.append(cleaned)

    if player_name:
        for language in ("da", "en", None):
            candidates.append(build_vman_player_url(player_id, player_name, language=language))

    candidates.extend([
        f"https://www.virtualmanager.com/da/players/{player_id}",
        f"https://www.virtualmanager.com/en/players/{player_id}",
        f"https://www.virtualmanager.com/players/{player_id}",
    ])

    unique: list[str] = []
    for url in candidates:
        if url and url not in unique:
            unique.append(url)
    return unique


def player_url_from_reference(reference: str, player_name: str | None = None) -> str:
    return player_url_candidates(reference, player_name=player_name)[0]


def training_url_candidates(reference: str, player_name: str | None = None) -> list[str]:
    """Return candidate URLs for a player's VMAN training history."""
    candidates: list[str] = []
    for profile_url in player_url_candidates(reference, player_name=player_name):
        if re.match(r"^https?://", profile_url, flags=re.IGNORECASE):
            candidates.append(profile_url.rstrip("/") + "/training")

    unique: list[str] = []
    for url in candidates:
        if url not in unique:
            unique.append(url)
    return unique


def fetch_training_html_from_reference(reference: str, session, player_name: str | None = None) -> tuple[str, str]:
    candidates = training_url_candidates(reference, player_name=player_name)
    last_error = None

    for url in candidates:
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return url, resp.text
        except Exception as error:
            last_error = error
            continue

    raise RuntimeError(f"Kunne ikke hente træningssiden. Sidste fejl: {last_error}")


def fetch_player_html_from_reference(reference: str, session, player_name: str | None = None) -> tuple[str, str]:
    candidates = player_url_candidates(reference, player_name=player_name)
    last_error = None

    for url in candidates:
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return url, resp.text
        except Exception as error:
            last_error = error
            continue

    raise RuntimeError(
        "Kunne ikke hente spilleren. VMAN kræver ofte det fulde spillerlink med navn/slug. "
        "Kopiér linket fra spillerprofilen og prøv igen. "
        f"Sidste fejl: {last_error}"
    )

def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _soup_from_html(html: str):
    try:
        from bs4 import BeautifulSoup
    except Exception as error:
        raise RuntimeError("BeautifulSoup mangler. Installer fx med: pip install beautifulsoup4") from error
    return BeautifulSoup(html, "html.parser")


def html_to_visible_text(html: str) -> str:
    soup = _soup_from_html(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    return normalize_spaces(soup.get_text(" ", strip=True))


def parse_money(value_text: str) -> Optional[int]:
    if not value_text:
        return None
    t = value_text.lower().strip()
    m = re.search(r"([\d.,\s]+)\s*(mio|million|m)\b", t)
    if m:
        num = m.group(1).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return int(float(num) * 1_000_000)
        except ValueError:
            pass
    digits = re.sub(r"[^\d]", "", value_text)
    if digits:
        try:
            return int(digits)
        except ValueError:
            return None
    return None


def parse_value_mio(value_text: str) -> Optional[float]:
    raw = parse_money(value_text)
    if raw is None:
        return None
    return round(raw / 1_000_000, 3)



def _iter_img_url_attributes(img):
    """Returnér mulige billed-URL'er fra et <img>-tag i prioriteret rækkefølge."""
    keys = ["src", "data-src", "data-original", "data-url", "data-lazy-src"]
    for key in keys:
        value = str(img.get(key) or "").strip()
        if value:
            yield value

    # srcset kan have form: "url1 1x, url2 2x". Brug sidste/største kandidat først.
    srcset_values = []
    for key in ["srcset", "data-srcset"]:
        raw = str(img.get(key) or "").strip()
        if not raw:
            continue
        for part in raw.split(","):
            url = part.strip().split(" ")[0].strip()
            if url:
                srcset_values.append(url)
    for value in reversed(srcset_values):
        yield value


def _avatar_candidate_records_from_html(html: str, base_url: str) -> list[dict[str, object]]:
    """Find og forklar robuste avatar-kandidater på VMANs profilside."""
    soup = _soup_from_html(html)
    records: list[dict[str, object]] = []

    for img_index, img in enumerate(soup.find_all("img"), start=1):
        classes = " ".join(str(c) for c in (img.get("class") or []))
        alt = str(img.get("alt") or "")
        title = str(img.get("title") or "")
        parent = img.parent
        parent_name = getattr(parent, "name", "") if parent is not None else ""
        try:
            parent_classes = " ".join(str(c) for c in (parent.get("class") or [])) if parent is not None else ""
        except Exception:
            parent_classes = ""
        try:
            parent_href = str(parent.get("href") or "") if parent is not None else ""
        except Exception:
            parent_href = ""

        for raw_src in _iter_img_url_attributes(img):
            src = raw_src.strip()
            if not src:
                continue
            text = " ".join([classes, src, alt, title, parent_classes, parent_href]).lower()
            score = 0
            reasons: list[str] = []
            penalties: list[str] = []

            # Den konkrete struktur i VMAN-debuggen:
            # <a href="/en/players/..."><img class="portrait" src="/assets/portraits/...jpg" /></a>
            if "portrait" in classes.lower():
                score += 120
                reasons.append("class=portrait")
            if "/assets/portraits/" in src.lower() or "/portraits/" in src.lower():
                score += 110
                reasons.append("portrait-path")
            if parent_href and re.search(r"/(?:da|en|no|sv|es)?/?players/\d+", parent_href, re.IGNORECASE):
                score += 35
                reasons.append("parent-player-link")
            if any(word in text for word in ["player", "spiller", "avatar", "profile", "profil"]):
                score += 20
                reasons.append("player/profile-context")
            if any(ext in src.lower().split("?")[0] for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
                score += 5
                reasons.append("image-extension")

            # Undgå sikre ikke-avatarer.
            if any(part in src.lower() for part in ["/flags/", "/icons/", "/layout/", "/moods/", "logo"]):
                score -= 200
                penalties.append("likely-ui-or-flag")

            records.append({
                "img_index": img_index,
                "score": score,
                "selected_candidate": score > 0,
                "reasons": reasons,
                "penalties": penalties,
                "src": src,
                "url": urljoin(base_url, src),
                "class": classes,
                "alt": alt,
                "title": title,
                "parent": parent_name,
                "parent_class": parent_classes,
                "parent_href": parent_href,
                "html": str(img)[:1200],
            })

    # Fallback: find /assets/portraits/... i rå HTML, hvis billedet ikke lå som almindeligt src.
    pattern = r'''["']([^"']*/assets/portraits/[^"']+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^"']*)?)["']'''
    for m in re.finditer(pattern, html, re.IGNORECASE):
        records.append({
            "img_index": None,
            "score": 100,
            "selected_candidate": True,
            "reasons": ["raw-html-portrait-path"],
            "penalties": [],
            "src": m.group(1),
            "url": urljoin(base_url, m.group(1)),
            "class": "",
            "alt": "",
            "title": "",
            "parent": "",
            "parent_class": "",
            "parent_href": "",
            "html": "",
        })

    records.sort(key=lambda item: (int(item.get("score") or 0), str(item.get("url") or "")), reverse=True)
    return records


def _avatar_candidate_urls_from_html(html: str, base_url: str) -> list[str]:
    """Find robuste avatar-kandidater på VMANs profilside."""
    seen = set()
    urls: list[str] = []
    for record in _avatar_candidate_records_from_html(html, base_url):
        if int(record.get("score") or 0) <= 0:
            continue
        url = str(record.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls

def parse_player_avatar_url_from_html(html: str, base_url: str) -> Optional[str]:
    """Find spillerens profilbillede/avatar på profilsiden."""
    urls = _avatar_candidate_urls_from_html(html, base_url)
    return urls[0] if urls else None


def fetch_avatar_image_bytes(avatar_url: str, session, referer: Optional[str] = None) -> tuple[bytes, Optional[str]]:
    """Hent avatarbilledets bytes. Kastes videre, hvis billedet ikke kan hentes."""
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": USER_AGENT,
    }
    if referer:
        headers["Referer"] = referer
    resp = session.get(avatar_url, timeout=REQUEST_TIMEOUT, headers=headers)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type")
    content = resp.content or b""
    lowered_type = str(content_type or "").lower()
    looks_like_image = (
        lowered_type.startswith("image/")
        or content.startswith(b"\xff\xd8")
        or content.startswith(b"\x89PNG")
        or content[:6] in {b"GIF87a", b"GIF89a"}
        or (content.startswith(b"RIFF") and b"WEBP" in content[:16])
    )
    if not looks_like_image:
        raise RuntimeError(f"Avatar-URL gav ikke et billedsvar ({content_type or 'ukendt content-type'}).")
    return content, content_type


def _avatar_file_suffix(content: bytes, content_type: Optional[str] = None, url: str = "") -> str:
    lowered_type = str(content_type or "").lower().split(";")[0].strip()
    if content.startswith(b"\xff\xd8") or lowered_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if content.startswith(b"\x89PNG") or lowered_type == "image/png":
        return ".png"
    if content[:6] in {b"GIF87a", b"GIF89a"} or lowered_type == "image/gif":
        return ".gif"
    if (content.startswith(b"RIFF") and b"WEBP" in content[:16]) or lowered_type == "image/webp":
        return ".webp"
    path = urlsplit(str(url or "")).path.lower()
    for suffix in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        if path.endswith(suffix):
            return ".jpg" if suffix == ".jpeg" else suffix
    return ".bin"


def format_avatar_debug_report(debug: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("VMAN avatar-debug")
    lines.append("=================")
    lines.append(f"Profil-URL: {debug.get('profile_url')}")
    lines.append(f"Valgt parser-URL: {debug.get('chosen_avatar_url')}")
    lines.append("")
    lines.append("Kandidatbilleder:")
    for rec in debug.get("candidate_records", [])[:80]:
        lines.append(
            f"- score={rec.get('score')} selected={rec.get('selected_candidate')} "
            f"img={rec.get('img_index')} class={rec.get('class')!r} src={rec.get('src')}"
        )
        lines.append(f"  url: {rec.get('url')}")
        if rec.get("reasons"):
            lines.append(f"  reasons: {', '.join(rec.get('reasons') or [])}")
        if rec.get("penalties"):
            lines.append(f"  penalties: {', '.join(rec.get('penalties') or [])}")
        if rec.get("parent_href"):
            lines.append(f"  parent_href: {rec.get('parent_href')}")
    lines.append("")
    lines.append("Downloadforsøg:")
    for item in debug.get("download_attempts", []):
        lines.append(
            f"- #{item.get('index')} ok={item.get('ok')} status={item.get('status_code')} "
            f"type={item.get('content_type')} bytes={item.get('bytes')} file={item.get('saved_file')}"
        )
        lines.append(f"  url: {item.get('url')}")
        if item.get("final_url") and item.get("final_url") != item.get("url"):
            lines.append(f"  final_url: {item.get('final_url')}")
        if item.get("error"):
            lines.append(f"  error: {item.get('error')}")
        if item.get("first_bytes_hex"):
            lines.append(f"  first_bytes_hex: {item.get('first_bytes_hex')}")
    return "\n".join(lines).strip() + "\n"


def try_extract_name(html: str, player_id: str) -> str:
    soup = _soup_from_html(html)
    h1 = soup.find("h1")
    if h1:
        txt = normalize_spaces(h1.get_text(" ", strip=True))
        txt = re.sub(r"^Player:\s*", "", txt, flags=re.IGNORECASE)
        if txt:
            return txt

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if title:
        title = re.sub(r"\s*[-|•].*$", "", title).strip()
        title = re.sub(r"^Player:\s*", "", title, flags=re.IGNORECASE)
        if title and str(player_id) not in title:
            return title

    text = html_to_visible_text(html)
    m = re.search(r"Player:\s*([A-ZÆØÅa-zæøå'´`\- ]+)", text)
    if m:
        return normalize_spaces(m.group(1))
    return "Ukendt"


def parse_precise_age_from_text(text: str) -> Optional[float]:
    age_match = re.search(r"\bAge\s*(\d{1,2})\b", text, re.IGNORECASE)
    if age_match:
        age = int(age_match.group(1))
        days_match = re.search(r"Birthday\s*in\s*(\d{1,2})\s*days", text, re.IGNORECASE)
        if days_match:
            days = int(days_match.group(1))
            return round(age + (30 - days) / 30, 2)
        if re.search(r"Birthday\s*tomorrow", text, re.IGNORECASE):
            return round(age + 29 / 30, 2)
        return float(age)

    age_match = re.search(r"\bAlder\s*(\d{1,2})\b", text, re.IGNORECASE)
    if age_match:
        age = int(age_match.group(1))
        days_match = re.search(r"Fødselsdag\s*om\s*(\d{1,2})\s*dage", text, re.IGNORECASE)
        if days_match:
            days = int(days_match.group(1))
            return round(age + (30 - days) / 30, 2)
        if re.search(r"Fødselsdag\s*i\s*morgen", text, re.IGNORECASE):
            return round(age + 29 / 30, 2)
        return float(age)

    return None


def position_value_candidates(value) -> list[str]:
    """Return position labels/codes from scalar, list or nested VMAN data.

    Club-list data has used both plain shorthand strings and richer objects.
    Keeping the extraction here makes single-player and group import share one
    position interpretation instead of maintaining two diverging mappings.
    """
    out: list[str] = []
    seen: set[str] = set()

    preferred_keys = (
        "position_code", "positionCode", "code", "short", "abbreviation",
        "position_short", "positionShort", "position_abbreviation",
        "positionAbbreviation", "position", "position_name", "positionName",
        "position_label", "positionLabel", "name", "label", "role", "slug",
    )

    def add(item):
        if item is None:
            return
        if isinstance(item, dict):
            used = set()
            for key in preferred_keys:
                if key in item:
                    used.add(key)
                    add(item.get(key))
            for key, nested in item.items():
                if key not in used:
                    add(nested)
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                add(nested)
            return
        text = str(item).strip()
        if not text or text.casefold() in {"none", "null", "nan"}:
            return
        marker = text.casefold()
        if marker not in seen:
            seen.add(marker)
            out.append(text)

    add(value)
    return out


def engine_position_from_code(code, default: str = "Forsvar") -> str:
    """Map VMAN position data to the four internal position groups.

    Important distinction: VMAN's Danish shorthand uses F/FC/FV/FH for
    Forsvar and A/AC/AV/AH for Angreb. A previous group-import mapping treated
    every F* value as English "forward", which reclassified defenders as
    attackers. Full English labels are still supported explicitly.
    """
    candidates = position_value_candidates(code)

    # Prefer explicit words over shorthand. This also handles objects such as
    # {"code": "F", "name": "Forward"} without guessing from the code alone.
    for raw in candidates:
        text = re.sub(r"[_-]+", " ", raw.casefold())
        if re.search(r"\b(goalkeeper|goal keeper|keeper|målmand|maalmand)\b", text):
            return "Keepere"
        if re.search(r"\b(midfielder|midfield|midtbane|central midfielder|defensive midfielder|attacking midfielder|offensive midfielder)\b", text):
            return "Midtbane"
        if re.search(r"\b(defender|defence|defense|forsvar|back|centre back|center back|full back|wing back)\b", text):
            return "Forsvar"
        if re.search(r"\b(forward|attacker|striker|angriber|angreb|centre forward|center forward|winger)\b", text):
            return "Angreb"

    keeper_codes = {"K", "GK", "G", "GOALKEEPER"}
    defender_codes = {
        "F", "FC", "FV", "FH",  # Danish VMAN: Forsvar
        "D", "DC", "DL", "DR", "DF", "CB", "LB", "RB", "LWB", "RWB",
    }
    midfielder_codes = {
        "M", "MC", "MD", "MO", "MV", "MH",  # Danish VMAN: Midtbane
        "MF", "CM", "DM", "AM", "LM", "RM",
    }
    attacker_codes = {
        "A", "AC", "AV", "AH",  # Danish VMAN: Angreb
        "FW", "FWD", "ST", "CF", "LW", "RW",
    }

    for raw in candidates:
        compact = re.sub(r"[^A-Z0-9]", "", raw.upper())
        if not compact:
            continue
        if compact in keeper_codes or compact.startswith("GK"):
            return "Keepere"
        if compact in defender_codes:
            return "Forsvar"
        if compact in midfielder_codes:
            return "Midtbane"
        if compact in attacker_codes:
            return "Angreb"

        # Safe family fallbacks for VMAN's Danish position abbreviations.
        if compact.startswith("K"):
            return "Keepere"
        if compact.startswith("A"):
            return "Angreb"
        if compact.startswith("M"):
            return "Midtbane"
        if compact.startswith("D"):
            return "Forsvar"
        if compact.startswith("F"):
            return "Forsvar"

    return default


STAT_ALIASES = {
    "Hurtighed": ["Hurtighed", "Speed", "Pace"],
    "Acceleration": ["Acceleration"],
    "Udholdenhed": ["Udholdenhed", "Stamina", "Endurance"],
    "Aflevering": ["Aflevering", "Passing", "Passes", "Pasninger"],
    "Afslutning": ["Afslutning", "Shooting", "Finishing"],
    "Dribling": ["Dribling", "Dribbling"],
    "Tackling": ["Tackling", "Tackles"],
    "Dødboldsituationer": ["Dødboldsituationer", "Dødbold", "Set pieces", "Set-pieces", "Setpieces", "Dead ball situations"],
    "Lederskab": ["Lederskab", "Leadership"],
    "Kampånd": ["Kampånd", "Fighting spirit", "Fight", "Team spirit", "Perseverance"],
    "Håndtering": ["Håndtering", "Handling"],
    "I luften": ["I luften", "Aerial ability", "Aerial", "In the air"],
    "Spring": ["Spring", "Diving", "Diving ability", "Jumping", "Jump"],
    "En mod en": ["En mod en", "One on ones", "One on one", "One-on-ones", "One-on-one"],
}


def _clean_stat_value(raw: str) -> Optional[float]:
    try:
        value = float(str(raw).replace(",", "."))
    except Exception:
        return None
    if 0 <= value <= 100:
        return round(value, 2)
    return None


def _alias_pattern(alias: str) -> str:
    # Tillad whitespace mellem ord og bindestreg/alm. mellemrum i engelske labels.
    escaped = re.escape(alias)
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"[-\s]*")
    return escaped


def _candidate_text_pairs_from_html(html: str) -> list[tuple[str, str]]:
    """Find label/value-par i tabeller, definition lists og simple HTML-naboer."""
    soup = _soup_from_html(html)
    pairs: list[tuple[str, str]] = []

    for row in soup.find_all("tr"):
        cells = [normalize_spaces(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            pairs.append((cells[0], cells[1]))
            pairs.append((cells[-1], cells[0]))

    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            pairs.append((normalize_spaces(dt.get_text(" ", strip=True)), normalize_spaces(dd.get_text(" ", strip=True))))

    # Nogle sider gengiver statnavn og værdi som nabo-elementer uden tabel.
    visible_nodes = []
    for tag in soup.find_all(["div", "span", "li", "p"]):
        txt = normalize_spaces(tag.get_text(" ", strip=True))
        if txt and len(txt) <= 80:
            visible_nodes.append(txt)
    for left, right in zip(visible_nodes, visible_nodes[1:]):
        pairs.append((left, right))
        pairs.append((right, left))

    return pairs


def parse_player_stats_from_html(html: str) -> dict[str, float]:
    """Udtræk individuelle egenskaber, hvis de står synligt i spillerprofilen.

    Parseren er bevidst tolerant, fordi VMAN kan vise labels på både dansk og
    engelsk og kan placere værdier i tabel, definition list eller almindelig tekst.
    """
    text = html_to_visible_text(html)
    stats: dict[str, float] = {}

    # 1) Først strukturerede label/value-par.
    pairs = _candidate_text_pairs_from_html(html)
    for stat, aliases in STAT_ALIASES.items():
        for alias in aliases:
            ap = _alias_pattern(alias)
            for label, value_text in pairs:
                if re.search(rf"(^|\b){ap}($|\b)", label, re.IGNORECASE):
                    m = re.search(r"\b(\d{1,3}(?:[.,]\d+)?)\b", value_text)
                    if m:
                        value = _clean_stat_value(m.group(1))
                        if value is not None:
                            stats[stat] = value
                            break
            if stat in stats:
                break

    # 2) Fallback på flad synlig tekst: "Stat 74" eller "74 Stat".
    for stat, aliases in STAT_ALIASES.items():
        if stat in stats:
            continue
        for alias in aliases:
            ap = _alias_pattern(alias)
            patterns = [
                rf"(^|[^\wæøåÆØÅ]){ap}\s*[:\-]?\s*(\d{{1,3}}(?:[.,]\d+)?)\b",
                rf"(^|[^\wæøåÆØÅ])(\d{{1,3}}(?:[.,]\d+)?)\s+{ap}($|[^\wæøåÆØÅ])",
            ]
            for pat in patterns:
                for m in re.finditer(pat, text, re.IGNORECASE):
                    raw_value = m.group(2)
                    value = _clean_stat_value(raw_value)
                    if value is not None:
                        stats[stat] = value
                        break
                if stat in stats:
                    break
            if stat in stats:
                break

    return stats


def parse_player_html(html: str, url: str) -> PlayerLinkSnapshot:
    player_id = extract_player_id(url)
    text = html_to_visible_text(html)
    navn = try_extract_name(html, player_id)

    rating = None
    for pat in [
        r"(\d+(?:[.,]\d+)?)\s+Rating",
        r"Rating\s+(\d+(?:[.,]\d+)?)",
        r"(\d+(?:[.,]\d+)?)\s+VT",
        r"VT\s+(\d+(?:[.,]\d+)?)",
    ]:
        matches = re.findall(pat, text, re.IGNORECASE)
        if matches:
            rating = float(matches[-1].replace(",", "."))
            break

    vaerdi_mio = None
    for pat in [
        r"Value\s+([\d,.\s]+)\s*C",
        r"Value\s*[:\-]?\s*([\d,.\s]+)",
        r"Værdi\s+([\d,.\s]+)\s*C",
        r"Værdi\s*[:\-]?\s*([\d,.\s]+)",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            vaerdi_mio = parse_value_mio(m.group(1))
            if vaerdi_mio is not None:
                break

    alder = parse_precise_age_from_text(text)
    stats = parse_player_stats_from_html(html)
    avatar_url = parse_player_avatar_url_from_html(html, url)

    position = "Ukendt"
    pos_patterns = [
        (r"Goalkeeper|Keeper|Målmand", "K"),
        (r"Defender,?\s*central|Forsvar,?\s*central", "FC"),
        (r"Defender,?\s*left|Forsvar,?\s*venstre", "FV"),
        (r"Defender,?\s*right|Forsvar,?\s*højre", "FH"),
        (r"Midfielder,?\s*defensive|Midtbane,?\s*defensiv", "MD"),
        (r"Midfielder,?\s*offensive|Midtbane,?\s*offensiv", "MO"),
        (r"Midfielder,?\s*central|Midtbane,?\s*central", "MC"),
        (r"Midfielder,?\s*left|Midtbane,?\s*venstre", "MV"),
        (r"Midfielder,?\s*right|Midtbane,?\s*højre", "MH"),
        (r"Forward,?\s*central|Angriber,?\s*central", "AC"),
        (r"Forward,?\s*left|Angriber,?\s*venstre", "AV"),
        (r"Forward,?\s*right|Angriber,?\s*højre", "AH"),
        (r"\bForward\b|\bAngriber\b", "AC"),
        (r"\bMidfielder\b|\bMidtbane\b", "MC"),
        (r"\bDefender\b|\bForsvar\b", "FC"),
    ]
    for pat, code in pos_patterns:
        if re.search(pat, text, re.IGNORECASE):
            position = code
            break

    return PlayerLinkSnapshot(
        navn=navn,
        alder=alder,
        vaerdi_mio=vaerdi_mio,
        rating=rating,
        position_code=position,
        engine_position=engine_position_from_code(position),
        url=url,
        stats=stats,
        avatar_url=avatar_url,
    )



def _all_stat_alias_patterns() -> list[str]:
    patterns = []
    for aliases in STAT_ALIASES.values():
        for alias in aliases:
            patterns.append(_alias_pattern(alias))
    # længste først, så "Dødboldsituationer" vinder over kortere delmatch
    patterns.sort(key=len, reverse=True)
    return patterns


def _next_stat_label_start(text: str, start: int) -> int:
    positions = []
    for ap in _all_stat_alias_patterns():
        m = re.search(rf"(^|[^\wæøåÆØÅ]){ap}($|[^\wæøåÆØÅ])", text[start:], re.IGNORECASE)
        if m:
            positions.append(start + m.start())
    return min(positions) if positions else len(text)


def _parse_stats_from_visible_text_ordered(text: str) -> dict[str, float]:
    """Parse VMANs egenskabspanel ud fra statnavn -> nærmeste efterfølgende tal.

    Den tidligere 1.92-parser kunne blive forvirret af nabo-elementer i DOM'en,
    så værdierne blev forskudt, og fx Aflevering/Kampånd blev stående på 2.
    Her læser vi i stedet hvert statnavns tekstvindue frem til næste statnavn.
    """
    text = normalize_spaces(text)
    stats: dict[str, float] = {}

    for stat, aliases in STAT_ALIASES.items():
        matches = []
        for alias in aliases:
            ap = _alias_pattern(alias)
            for m in re.finditer(rf"(^|[^\wæøåÆØÅ]){ap}($|[^\wæøåÆØÅ])", text, re.IGNORECASE):
                matches.append((m.start(), m.end()))
        if not matches:
            continue

        # Vælg første synlige forekomst og læs frem til næste stat-label.
        start, end = sorted(matches, key=lambda item: item[0])[0]
        next_start = _next_stat_label_start(text, end)
        window = text[end:next_start]
        if len(window) > 140:
            window = window[:140]

        numbers = []
        for n in re.finditer(r"\b(\d{1,3}(?:[.,]\d+)?)\b", window):
            value = _clean_stat_value(n.group(1))
            if value is not None:
                numbers.append(value)

        if numbers:
            # Brug sidste tal i vinduet, da enkelte HTML-udsnit kan indeholde
            # små bar-/procenttal før selve egenskabsværdien.
            stats[stat] = numbers[-1]

    return stats


def _safe_structured_stat_pairs(html: str) -> dict[str, float]:
    """Fallback: kun tabel-/definition-list-par, ikke vilkårlige nabo-divs."""
    soup = _soup_from_html(html)
    pairs: list[tuple[str, str]] = []

    for row in soup.find_all("tr"):
        cells = [normalize_spaces(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            pairs.append((cells[0], cells[1]))

    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            pairs.append((normalize_spaces(dt.get_text(" ", strip=True)), normalize_spaces(dd.get_text(" ", strip=True))))

    stats: dict[str, float] = {}
    for stat, aliases in STAT_ALIASES.items():
        for alias in aliases:
            ap = _alias_pattern(alias)
            for label, value_text in pairs:
                # Label skal være tæt på selve aliaset; ellers kan hele
                # egenskabspanelet som container blive fejltolket.
                if len(label) > len(alias) + 18:
                    continue
                if re.search(rf"(^|[^\wæøåÆØÅ]){ap}($|[^\wæøåÆØÅ])", label, re.IGNORECASE):
                    nums = [
                        _clean_stat_value(m.group(1))
                        for m in re.finditer(r"\b(\d{1,3}(?:[.,]\d+)?)\b", value_text)
                    ]
                    nums = [n for n in nums if n is not None]
                    if nums:
                        stats[stat] = nums[-1]
                        break
            if stat in stats:
                break
    return stats



def _canonical_stat_from_label_exact(label: str) -> str | None:
    label_norm = normalize_spaces(label).lower()
    if not label_norm:
        return None

    for stat, aliases in STAT_ALIASES.items():
        for alias in aliases:
            if label_norm == normalize_spaces(alias).lower():
                return stat

    # Konservativ fallback: næsten-exact labels.
    for stat, aliases in STAT_ALIASES.items():
        for alias in aliases:
            alias_norm = normalize_spaces(alias).lower()
            if len(label_norm) <= len(alias_norm) + 8 and alias_norm in label_norm:
                return stat

    return None


def _parse_stats_from_vman_abilities_table(html: str) -> dict[str, float]:
    """Præcis parser for VMANs offentlige spillerprofil.

    Debug-HTML'en viser strukturen:
    div.stats > table[data-player-id] > tbody > tr > td > div.content
      div.label = statnavn, fx "Passing"
      div.value = selve egenskabstallet, fx "41"

    Vigtigt: der findes også td.value inde i progress_description, så vi tager
    kun direkte child: content.find(..., recursive=False).
    """
    soup = _soup_from_html(html)
    stats: dict[str, float] = {}

    containers = list(soup.select("div.stats table[data-player-id] div.content"))
    if not containers:
        containers = list(soup.select("div.stats div.content"))

    for content in containers:
        label_el = content.find("div", class_="label", recursive=False)
        value_el = content.find("div", class_="value", recursive=False)
        if label_el is None or value_el is None:
            continue

        label = normalize_spaces(label_el.get_text(" ", strip=True))
        stat = _canonical_stat_from_label_exact(label)
        if stat is None:
            continue

        raw_value = normalize_spaces(value_el.get_text(" ", strip=True))
        m = re.search(r"\b(\d{1,3}(?:[.,]\d+)?)\b", raw_value)
        if not m:
            continue

        value = _clean_stat_value(m.group(1))
        if value is not None:
            stats[stat] = value

    return stats


def parse_player_stats_from_html(html: str) -> dict[str, float]:
    """Udtræk individuelle egenskaber fra VMANs HTML.

    1.95: Førstevalg er selector-baseret parser på VMANs rigtige abilities-tabel:
    div.stats table[data-player-id] div.content.

    Den gamle tekstbaserede parser bruges kun som fallback, hvis strukturen
    ændrer sig eller ikke findes.
    """
    exact = _parse_stats_from_vman_abilities_table(html)
    if exact:
        return exact

    text = html_to_visible_text(html)
    ordered = _parse_stats_from_visible_text_ordered(text)
    if len(ordered) >= 6:
        return ordered

    structured = _safe_structured_stat_pairs(html)
    merged = dict(ordered)
    for stat, value in structured.items():
        merged.setdefault(stat, value)
    return merged

TRAINING_XP_LABEL_RE = re.compile(
    r"\b(?:xp|trænings[-\s]*xp|training[-\s]*xp|træning[-\s]*xp|xp[-\s]*(?:gain|udbytte)|(?:gain|udbytte)[-\s]*xp)\b",
    re.IGNORECASE,
)

# VMAN viser også spillerens generelle "Erfaring" på spiller-/træningssider.
# Den må ikke blandes sammen med trænings-XP. I 2.25 kunne netop den type tal
# blive fanget og give alt for lave gennemsnit, fx omkring 22-23 i stedet for 140+.
TRAINING_XP_VALUE_MIN = 80.0
TRAINING_XP_VALUE_MAX = 350.0
TRAINING_TABLE_CONTEXT_RE = re.compile(
    r"\b(?:træning|traening|training|seneste|historik|history|dato|date|øvelse|oevelse|exercise)\b",
    re.IGNORECASE,
)
TRAINING_XP_BAD_CONTEXT_RE = re.compile(
    r"\b(?:erfaring|experience|alder|age|potentiale|potential|værdi|vaerdi|value|vurdering|rating|lønn?|loenn?|salary)\b",
    re.IGNORECASE,
)
TRAINING_XP_SOFT_COLUMN_RE = re.compile(
    r"\b(?:træningspræstation|traeningspraestation|præstation|praestation|performance|resultat|result|udbytte|gain|point|points|score)\b",
    re.IGNORECASE,
)


def _clean_training_xp_value(raw: str, *, plausible: bool = True) -> Optional[float]:
    """Konservativ nummer-parser til trænings-XP.

    Trænings-XP ligger normalt langt over almindelige spiller-"erfaring"-tal.
    Derfor bruger vi et plausibilitetsfilter som standard for at undgå, at
    spillerens erfaring/potentiale/alder bliver importeret som XP.
    """
    if raw is None:
        return None
    s = normalize_spaces(str(raw))
    if not s:
        return None

    # Bevar tusindtalsseparatorer ude af billedet, men accepter dansk komma.
    m = re.search(r"[-+]?\d{1,4}(?:[.,]\d{1,2})?", s)
    if not m:
        return None
    token = m.group(0).replace("+", "").replace(",", ".")
    try:
        value = float(token)
    except Exception:
        return None

    if plausible and not (TRAINING_XP_VALUE_MIN <= value <= TRAINING_XP_VALUE_MAX):
        return None
    if 0 < value <= 999:
        return round(value, 2)
    return None


def _extract_training_xp_from_text(text: str) -> Optional[float]:
    text = normalize_spaces(text)
    if not text:
        return None

    # Vigtigt: Match kun XP/trænings-XP, ikke "erfaring/experience" alene.
    patterns = [
        r"(?:trænings[-\s]*xp|training[-\s]*xp|træning[-\s]*xp|xp[-\s]*(?:gain|udbytte)|(?:gain|udbytte)[-\s]*xp|\bxp\b)\s*[:=+]?\s*([-+]?\d{1,4}(?:[.,]\d{1,2})?)",
        r"([-+]?\d{1,4}(?:[.,]\d{1,2})?)\s*(?:trænings[-\s]*xp|training[-\s]*xp|træning[-\s]*xp|xp[-\s]*(?:gain|udbytte)|\bxp\b)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = _clean_training_xp_value(m.group(1), plausible=True)
            if value is not None:
                return value
    return None


def _numeric_training_candidate(text: str) -> Optional[float]:
    if TRAINING_XP_BAD_CONTEXT_RE.search(normalize_spaces(text)):
        return None
    return _clean_training_xp_value(text, plausible=True)


def _table_rows_as_cells(table) -> list[list[str]]:
    rows = []
    for row in table.find_all("tr"):
        cells = [normalize_spaces(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def _dedupe_training_values(values: list[float]) -> list[float]:
    # Bevar rækkefølge; gentagne XP-tal kan sagtens være rigtige forskellige træninger.
    cleaned = []
    for value in values:
        if value is None:
            continue
        cleaned.append(round(float(value), 2))
        if len(cleaned) >= 60:
            break
    return cleaned[:60]


def _training_column_score(header: str) -> int:
    header = normalize_spaces(header).lower()
    if not header:
        return 0
    if TRAINING_XP_BAD_CONTEXT_RE.search(header) and not re.search(r"\bxp\b", header, re.IGNORECASE):
        return 0
    if TRAINING_XP_LABEL_RE.search(header):
        return 100
    if re.search(r"træningspræstation|traeningspraestation|præstation|praestation|performance", header, re.IGNORECASE):
        return 90
    if re.search(r"udbytte|gain|resultat|result", header, re.IGNORECASE):
        return 70
    if re.search(r"point|points|score", header, re.IGNORECASE):
        return 45
    return 0


def _training_column_values(rows: list[list[str]], idx: int) -> list[float]:
    values: list[float] = []
    for cells in rows[1:]:
        if idx < len(cells):
            value = _numeric_training_candidate(cells[idx])
            if value is not None:
                values.append(value)
        if len(values) >= 60:
            break
    return _dedupe_training_values(values)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)

def _line_is_training_total_label(line: str) -> bool:
    return bool(re.fullmatch(r"(?:total|i alt|ialt|samlet)", normalize_spaces(line), re.IGNORECASE))


def _parse_training_total_xp_from_tables(soup) -> list[float]:
    """Find totalsummen for hver træning i VMANs træningstabeller.

    I VMANs HTML ligger hver træning typisk som en lille tabel/card hvor
    første række er `Total | +148 xp`, efterfulgt af de enkelte egenskaber.
    Det er denne Total-række, ikke de enkelte egenskabslinjer, der skal bruges
    til gennemsnittet.
    """
    values: list[float] = []
    for table in soup.find_all("table"):
        rows = _table_rows_as_cells(table)
        for cells in rows:
            if len(cells) >= 2 and _line_is_training_total_label(cells[0]):
                value = _extract_training_xp_from_text(cells[1])
                if value is None:
                    value = _clean_training_xp_value(cells[1], plausible=True)
                if value is not None:
                    values.append(value)
                    break
        if len(values) >= 60:
            break
    return _dedupe_training_values(values)


def _parse_training_total_xp_from_visible_lines(html: str) -> list[float]:
    """Find `Total` efterfulgt af `+NN xp` i den synlige træningstekst."""
    soup = _soup_from_html(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()

    raw_lines = [normalize_spaces(line) for line in soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in raw_lines if line]
    values: list[float] = []

    for i, line in enumerate(lines):
        value = None
        # Variant 1: "Total +148 xp" på samme linje.
        same_line = re.search(
            r"\b(?:total|i alt|ialt|samlet)\b\s*[:=]?\s*([-+]?\d{1,4}(?:[.,]\d{1,2})?\s*xp)",
            line,
            re.IGNORECASE,
        )
        if same_line:
            value = _extract_training_xp_from_text(same_line.group(1))

        # Variant 2: "Total" på én linje og "+148 xp" på næste linje.
        if value is None and _line_is_training_total_label(line) and i + 1 < len(lines):
            next_line = lines[i + 1]
            if TRAINING_XP_LABEL_RE.search(next_line):
                value = _extract_training_xp_from_text(next_line)

        if value is not None:
            values.append(value)
            if len(values) >= 60:
                break

    return _dedupe_training_values(values)


def _parse_training_summary_average_from_html(html: str) -> Optional[float]:
    """Find et direkte gennemsnit på træningssiden, hvis VMAN viser det.

    Hvis siden allerede skriver fx "Gennemsnitlig træningspræstation: 148",
    er det mere sikkert end at gætte den rigtige kolonne i tabellen.
    Funktionen er bevidst konservativ: "gennemsnit/average" skal optræde
    tæt på træning/præstation/XP-ord, og værdien skal ligge i XP-intervallet.
    """
    soup = _soup_from_html(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    visible = soup.get_text("\n", strip=True)
    lines = [normalize_spaces(line) for line in visible.splitlines() if normalize_spaces(line)]

    avg_re = re.compile(r"\b(?:gennemsnit(?:lig|ligt)?|average|avg\.?|snit)\b", re.IGNORECASE)
    training_re = re.compile(
        r"\b(?:træning|traening|training|træningspræstation|traeningspraestation|præstation|praestation|performance|xp|udbytte|gain)\b",
        re.IGNORECASE,
    )
    number_re = re.compile(r"[-+]?\d{1,4}(?:[.,]\d{1,2})?")

    candidates: list[tuple[int, float, str]] = []
    for i, line in enumerate(lines):
        context = " ".join(lines[max(0, i - 1): min(len(lines), i + 2)])
        if not avg_re.search(context) or not training_re.search(context):
            continue
        for m in number_re.finditer(context):
            value = _clean_training_xp_value(m.group(0), plausible=True)
            if value is None:
                continue
            # Prioritér tal på samme linje som gennemsnitsordet.
            score = 2 if avg_re.search(line) and training_re.search(line) else 1
            candidates.append((score, value, context[:700]))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][1]


def _training_summary_average_candidates_from_html(html: str) -> list[dict[str, object]]:
    """Debug-liste over mulige direkte gennemsnitsangivelser på siden."""
    soup = _soup_from_html(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    lines = [normalize_spaces(line) for line in soup.get_text("\n", strip=True).splitlines() if normalize_spaces(line)]
    avg_re = re.compile(r"\b(?:gennemsnit(?:lig|ligt)?|average|avg\.?|snit)\b", re.IGNORECASE)
    training_re = re.compile(
        r"\b(?:træning|traening|training|træningspræstation|traeningspraestation|præstation|praestation|performance|xp|udbytte|gain)\b",
        re.IGNORECASE,
    )
    number_re = re.compile(r"[-+]?\d{1,4}(?:[.,]\d{1,2})?")
    out: list[dict[str, object]] = []
    for i, line in enumerate(lines):
        context = " ".join(lines[max(0, i - 1): min(len(lines), i + 2)])
        if not avg_re.search(context) or not training_re.search(context):
            continue
        numbers = []
        for m in number_re.finditer(context):
            value = _clean_training_xp_value(m.group(0), plausible=False)
            if value is not None and 0 <= value <= 400:
                numbers.append(value)
        if numbers:
            out.append({"line": i + 1, "numbers_0_400": numbers, "context": context[:1000]})
            if len(out) >= 80:
                break
    return out


def _tsv_cell(text: object) -> str:
    return normalize_spaces(str(text if text is not None else "")).replace("\t", " ").replace("\n", " ")


def training_table_matrix_tsv_from_html(html: str) -> str:
    """Rå tabelmatrix til debug: alle celler fra alle træningstabeller."""
    soup = _soup_from_html(html)
    lines = ["table\trow\tcol\theader\tcell\tplausible_ge_80\traw_numbers_0_400"]
    number_re = re.compile(r"[-+]?\d{1,4}(?:[.,]\d{1,2})?")
    for table_index, table in enumerate(soup.find_all("table"), start=1):
        rows = _table_rows_as_cells(table)
        if not rows:
            continue
        headers = rows[0]
        for row_index, cells in enumerate(rows, start=1):
            for col_index, cell in enumerate(cells, start=1):
                header = headers[col_index - 1] if col_index - 1 < len(headers) else ""
                plausible = _clean_training_xp_value(cell, plausible=True)
                nums = []
                for m in number_re.finditer(cell):
                    value = _clean_training_xp_value(m.group(0), plausible=False)
                    if value is not None and 0 <= value <= 400:
                        nums.append(str(value))
                lines.append(
                    f"{table_index}\t{row_index}\t{col_index}\t{_tsv_cell(header)}\t{_tsv_cell(cell)}\t{'' if plausible is None else plausible}\t{','.join(nums)}"
                )
    return "\n".join(lines).rstrip() + "\n"


def training_numeric_candidates_tsv_from_html(html: str) -> str:
    """Én linje pr. numerisk kandidatcelle med række-kontekst."""
    soup = _soup_from_html(html)
    lines = ["table\trow\tcol\theader\tvalue\tplausible_ge_80\tcell\trow_context"]
    number_re = re.compile(r"[-+]?\d{1,4}(?:[.,]\d{1,2})?")
    for table_index, table in enumerate(soup.find_all("table"), start=1):
        rows = _table_rows_as_cells(table)
        if not rows:
            continue
        headers = rows[0]
        for row_index, cells in enumerate(rows[1:], start=2):
            row_context = " | ".join(cells)
            for col_index, cell in enumerate(cells, start=1):
                header = headers[col_index - 1] if col_index - 1 < len(headers) else ""
                for m in number_re.finditer(cell):
                    value = _clean_training_xp_value(m.group(0), plausible=False)
                    if value is None or not (0 <= value <= 400):
                        continue
                    plausible = _clean_training_xp_value(m.group(0), plausible=True)
                    lines.append(
                        f"{table_index}\t{row_index}\t{col_index}\t{_tsv_cell(header)}\t{value}\t{'' if plausible is None else plausible}\t{_tsv_cell(cell)}\t{_tsv_cell(row_context)}"
                    )
    return "\n".join(lines).rstrip() + "\n"


def _parse_training_xp_from_tables(soup) -> list[float]:
    """Udtræk XP fra tabeller.

    2.27: Vælg ikke bare første numeriske "point"-kolonne. VMANs
    træningsside kan også indeholde lave tal (fx 40), som ligner point men
    ikke er den gennemsnitlige trænings-XP. Derfor scores kandidatkolonner,
    og værdier under 80 filtreres fra som standard.
    """
    best_values: list[float] = []
    best_score = -1
    best_count = -1
    best_avg = -1.0

    for table in soup.find_all("table"):
        rows = _table_rows_as_cells(table)
        if len(rows) < 2:
            continue

        table_text = normalize_spaces(table.get_text(" ", strip=True))
        headers = rows[0]
        max_cols = max((len(row) for row in rows), default=0)

        # Stærkeste signal: eksplicit XP-kolonne. Men returnér kun, hvis den
        # giver en ordentlig mængde plausible værdier.
        candidate_columns: list[tuple[int, int, str]] = []
        for idx in range(max_cols):
            header = headers[idx] if idx < len(headers) else ""
            score = _training_column_score(header)
            if score > 0:
                candidate_columns.append((score, idx, header))

        # Hvis der ikke findes en eksplicit XP-kolonne, tilføjer vi også
        # numeriske kolonner i træningstabeller som lavere rangerede kandidater.
        # Det er nødvendigt fordi VMAN kan vise den relevante træningsværdi i en
        # kolonne uden tydelig XP-header, mens en nærliggende "Præstation"-kolonne
        # kan give et forkert, men plausibelt, gennemsnit.
        has_explicit_xp_header = any(score >= 100 for score, _idx, _header in candidate_columns)
        if not has_explicit_xp_header and len(rows) >= 10 and TRAINING_TABLE_CONTEXT_RE.search(table_text):
            existing_indices = {idx for _score, idx, _header in candidate_columns}
            for idx in range(max_cols):
                header = headers[idx] if idx < len(headers) else ""
                if idx in existing_indices:
                    continue
                if TRAINING_XP_BAD_CONTEXT_RE.search(header):
                    continue
                # Numeriske kolonner kan være kandidater, men får lav score.
                values = _training_column_values(rows, idx)
                if len(values) >= 10 and (_average(values) or 0) >= TRAINING_XP_VALUE_MIN:
                    candidate_columns.append((20, idx, header or f"kolonne {idx + 1}"))

        # Hvis der findes en eksplicit XP-kolonne, skal den vinde. Hvis siden
        # derimod kun har blødere labels som "præstation"/"resultat" eller
        # uklare numeriske kolonner, vælger vi den kandidat med 60-ish værdier
        # og højest gennemsnit. Det undgår at låse på en nærliggende, men forkert
        # kolonne, hvilket gav ca. 139 i stedet for den forventede 148.
        has_explicit_xp_column = any(score >= 100 for score, _idx, _header in candidate_columns)

        for score, idx, header in sorted(candidate_columns, reverse=True):
            values = _training_column_values(rows, idx)
            if len(values) < 10:
                continue
            avg = _average(values) or 0
            # Ekstra værn mod at vælge fx 40-point/energi-kolonner.
            if avg < TRAINING_XP_VALUE_MIN:
                continue

            if has_explicit_xp_column:
                rank = (score, len(values), avg)
                best_rank = (best_score, best_count, best_avg)
            else:
                # Uden eksplicit XP-label: flest værdier først, derefter højeste
                # plausible gennemsnit, derefter label-score som tie-breaker.
                rank = (min(len(values), 60), avg, score)
                best_rank = (min(best_count, 60), best_avg, best_score)

            if rank > best_rank:
                best_score = score
                best_count = len(values)
                best_avg = avg
                best_values = values
                if has_explicit_xp_column and score >= 100 and len(values) >= 60:
                    return best_values[:60]

        # Række-/cellebaseret fallback, men kun hvis XP nævnes i selve rækken.
        values = []
        for cells in rows:
            row_text = normalize_spaces(" ".join(cells))
            if not TRAINING_XP_LABEL_RE.search(row_text):
                continue
            value = _extract_training_xp_from_text(row_text)
            if value is not None:
                values.append(value)
            if len(values) >= 60:
                break
        if len(values) >= 10 and (_average(values) or 0) >= TRAINING_XP_VALUE_MIN:
            return _dedupe_training_values(values)

    return best_values[:60]

def _parse_training_xp_from_data_attrs_and_classes(soup) -> list[float]:
    values: list[float] = []
    seen_elements = set()

    for tag in soup.find_all(True):
        attrs = getattr(tag, "attrs", {}) or {}
        cls = attrs.get("class")
        class_text = " ".join(cls) if isinstance(cls, list) else str(cls or "")
        id_text = str(attrs.get("id", ""))
        attr_keys = " ".join(str(k) for k in attrs.keys())
        meta = f"{class_text} {id_text} {attr_keys}"
        if not TRAINING_XP_LABEL_RE.search(meta):
            continue
        if TRAINING_XP_BAD_CONTEXT_RE.search(meta) and not re.search(r"\bxp\b", meta, re.IGNORECASE):
            continue

        key = id(tag)
        if key in seen_elements:
            continue
        seen_elements.add(key)

        found = None
        for attr_name, attr_value in attrs.items():
            if TRAINING_XP_LABEL_RE.search(str(attr_name)):
                found = _clean_training_xp_value(str(attr_value), plausible=True)
                if found is not None:
                    break
        if found is None:
            txt = normalize_spaces(tag.get_text(" ", strip=True))
            found = _extract_training_xp_from_text(txt)
        if found is not None:
            values.append(found)
            if len(values) >= 60:
                break

    return _dedupe_training_values(values)


def _parse_training_xp_from_scripts(soup) -> list[float]:
    values: list[float] = []
    key_re = re.compile(
        r"[\"'](?:xp|training_xp|trainingXp|trainingXP|xp_gain|xpGain|xpGainValue|training_xp_gain)[\"']\s*:\s*[\"']?([-+]?\d{1,4}(?:[.,]\d{1,2})?)",
        re.IGNORECASE,
    )

    for script in soup.find_all("script"):
        script_text = script.string or script.get_text("\n", strip=False) or ""
        if not script_text:
            continue
        for m in key_re.finditer(script_text):
            value = _clean_training_xp_value(m.group(1), plausible=True)
            if value is not None:
                values.append(value)
                if len(values) >= 60:
                    return _dedupe_training_values(values)

    return _dedupe_training_values(values)


def _parse_training_xp_from_visible_lines(html: str) -> list[float]:
    soup = _soup_from_html(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()

    values: list[float] = []
    raw_lines = soup.get_text("\n", strip=True).splitlines()
    for line in raw_lines:
        line = normalize_spaces(line)
        if not line or not TRAINING_XP_LABEL_RE.search(line):
            continue
        if TRAINING_XP_BAD_CONTEXT_RE.search(line) and not re.search(r"\bxp\b", line, re.IGNORECASE):
            continue
        value = _extract_training_xp_from_text(line)
        if value is not None:
            values.append(value)
            if len(values) >= 60:
                break
    return _dedupe_training_values(values)


def parse_training_xp_values_from_html(html: str) -> list[float]:
    """Udtræk XP-tal fra VMANs side med de seneste træninger."""
    soup = _soup_from_html(html)

    # 2.30: VMANs træningsside viser hver træning som en Total-række
    # efterfulgt af egenskabsfordelingen. Brug Total-værdierne først; ellers
    # kan parseren blande graf-aksetal og enkelte egenskabs-XP ind og ende
    # omkring 139 i stedet for ca. 148.
    for parser in (
        _parse_training_total_xp_from_tables,
        lambda _soup: _parse_training_total_xp_from_visible_lines(html),
        _parse_training_xp_from_tables,
        _parse_training_xp_from_scripts,
        _parse_training_xp_from_data_attrs_and_classes,
    ):
        try:
            values = parser(soup)
        except Exception:
            values = []
        if values:
            return values[:60]

    return _parse_training_xp_from_visible_lines(html)[:60]


def debug_training_xp_candidates_from_html(html: str) -> dict[str, object]:
    """Lav en læsbar kandidatoversigt, så HTML-strukturen kan fejlrettes."""
    soup = _soup_from_html(html)
    table_debug: list[dict[str, object]] = []

    for table_index, table in enumerate(soup.find_all("table"), start=1):
        rows = _table_rows_as_cells(table)
        if not rows:
            continue
        headers = rows[0]
        max_cols = max((len(row) for row in rows), default=0)
        columns = []
        for idx in range(max_cols):
            header = headers[idx] if idx < len(headers) else ""
            values = _training_column_values(rows, idx)
            raw_preview = []
            for cells in rows[1:8]:
                raw_preview.append(cells[idx] if idx < len(cells) else "")
            columns.append({
                "index": idx + 1,
                "header": header,
                "score": _training_column_score(header),
                "count_plausible_ge_80": len(values),
                "average_plausible_ge_80": _average(values),
                "sum_first_60_plausible_ge_80": round(sum(values[:60]), 2) if values else 0.0,
                "first_values_plausible_ge_80": values[:20],
                "raw_preview_first_rows": raw_preview,
            })
        table_debug.append({
            "table_index": table_index,
            "row_count": len(rows),
            "headers": headers,
            "columns": columns,
            "first_rows": rows[:8],
            "text_preview": normalize_spaces(table.get_text(" ", strip=True))[:1200],
        })

    visible_lines = []
    raw_lines = soup.get_text("\n", strip=True).splitlines()
    number_re = re.compile(r"[-+]?\d{1,4}(?:[.,]\d{1,2})?")
    keyword_re = re.compile(
        r"træning|traening|training|xp|præstation|praestation|performance|resultat|udbytte|gain|point|score",
        re.IGNORECASE,
    )
    for line_no, line in enumerate(raw_lines, start=1):
        line = normalize_spaces(line)
        if not line:
            continue
        nums = []
        for m in number_re.finditer(line):
            value = _clean_training_xp_value(m.group(0), plausible=False)
            if value is not None and 0 <= value <= 400:
                nums.append(value)
        if nums and (keyword_re.search(line) or any(v >= TRAINING_XP_VALUE_MIN for v in nums)):
            visible_lines.append({"line": line_no, "numbers_0_400": nums, "text": line[:600]})
            if len(visible_lines) >= 240:
                break

    parsed = parse_training_xp_values_from_html(html)
    total_values_from_tables = _parse_training_total_xp_from_tables(soup)
    total_values_from_visible_lines = _parse_training_total_xp_from_visible_lines(html)
    direct_summary_average = _parse_training_summary_average_from_html(html)
    summary_average_candidates = _training_summary_average_candidates_from_html(html)

    # Kandidatkolonner sorteret som læsehjælp: dette er ofte den hurtigste måde
    # at se, hvilken kolonne der ligner de 60 træningspræstationer.
    ranked_columns = []
    for table in table_debug:
        if not isinstance(table, dict):
            continue
        for col in table.get("columns", []):
            if not isinstance(col, dict):
                continue
            count = int(col.get("count_plausible_ge_80") or 0)
            avg = col.get("average_plausible_ge_80")
            if count >= 10 and avg is not None:
                ranked_columns.append({
                    "table_index": table.get("table_index"),
                    "column_index": col.get("index"),
                    "header": col.get("header"),
                    "score": col.get("score"),
                    "count": count,
                    "average": avg,
                    "sum_first_60": col.get("sum_first_60_plausible_ge_80"),
                    "first_values": col.get("first_values_plausible_ge_80"),
                    "raw_preview_first_rows": col.get("raw_preview_first_rows"),
                })
    ranked_columns.sort(key=lambda item: (min(int(item.get("count") or 0), 60), float(item.get("average") or 0), int(item.get("score") or 0)), reverse=True)

    return {
        "parser_settings": {
            "min_plausible_xp": TRAINING_XP_VALUE_MIN,
            "max_plausible_xp": TRAINING_XP_VALUE_MAX,
            "target_count": 60,
        },
        "direct_summary_average": direct_summary_average,
        "summary_average_candidates": summary_average_candidates,
        "total_values_from_tables": total_values_from_tables,
        "total_values_from_visible_lines": total_values_from_visible_lines,
        "chosen_values": parsed[:60],
        "chosen_count": len(parsed[:60]),
        "chosen_average": _average(parsed[:60]),
        "ranked_column_candidates": ranked_columns[:40],
        "tables": table_debug,
        "visible_line_candidates": visible_lines,
    }


def format_training_xp_debug_report(debug: dict[str, object]) -> str:
    lines: list[str] = []
    settings = debug.get("parser_settings", {}) if isinstance(debug, dict) else {}
    lines.append("VMAN trænings-XP kandidatoversigt")
    lines.append(f"Plausible XP-tal: {settings.get('min_plausible_xp')}–{settings.get('max_plausible_xp')}; mål: 60 træninger")
    lines.append("")
    lines.append(f"Direkte gennemsnit fundet: {debug.get('direct_summary_average')}")
    if debug.get("summary_average_candidates"):
        lines.append("Mulige direkte gennemsnitslinjer:")
        for item in debug.get("summary_average_candidates", [])[:20]:
            if isinstance(item, dict):
                lines.append(f"  line {item.get('line')}: nums={item.get('numbers_0_400')} · {item.get('context')}")
    lines.append(f"Valgt af parser: count={debug.get('chosen_count')} average={debug.get('chosen_average')} values={debug.get('chosen_values')}")
    if debug.get("total_values_from_tables"):
        vals = debug.get("total_values_from_tables") or []
        lines.append(f"Total-rækker fra tabeller: count={len(vals)} average={_average(vals) if vals else None} values={vals[:60]}")
    if debug.get("total_values_from_visible_lines"):
        vals = debug.get("total_values_from_visible_lines") or []
        lines.append(f"Total-rækker fra synlig tekst: count={len(vals)} average={_average(vals) if vals else None} values={vals[:60]}")
    lines.append("")
    lines.append("RANGEREDE KANDIDATKOLONNER")
    for item in debug.get("ranked_column_candidates", [])[:40]:
        if isinstance(item, dict):
            lines.append(
                f"table {item.get('table_index')} col {item.get('column_index')} "
                f"header={item.get('header')!r} score={item.get('score')} "
                f"count={item.get('count')} avg={item.get('average')} "
                f"sum60={item.get('sum_first_60')} values={item.get('first_values')} "
                f"raw={item.get('raw_preview_first_rows')}"
            )
    lines.append("")

    for table in debug.get("tables", [])[:20]:
        lines.append(f"TABLE {table.get('table_index')} · rows={table.get('row_count')} · headers={table.get('headers')}")
        for col in table.get("columns", []):
            if not isinstance(col, dict):
                continue
            if col.get("score") or col.get("count_plausible_ge_80"):
                lines.append(
                    "  "
                    f"col {col.get('index')} header={col.get('header')!r} "
                    f"score={col.get('score')} count={col.get('count_plausible_ge_80')} "
                    f"avg={col.get('average_plausible_ge_80')} "
                    f"values={col.get('first_values_plausible_ge_80')} "
                    f"raw={col.get('raw_preview_first_rows')}"
                )
        lines.append(f"  first_rows={table.get('first_rows')}")
        lines.append("")
    lines.append("SYNLIGE LINJER MED KANDIDATTAL")
    for item in debug.get("visible_line_candidates", [])[:160]:
        if isinstance(item, dict):
            lines.append(f"line {item.get('line')}: nums={item.get('numbers_0_400')} · {item.get('text')}")
    return "\n".join(lines).strip() + "\n"


def summarize_training_xp_from_html(html: str) -> dict[str, object]:
    direct_average = _parse_training_summary_average_from_html(html)
    if direct_average is not None:
        avg = round(float(direct_average), 2)
        return {
            "values": [avg] * 60,
            "count": 60,
            "total": round(avg * 60, 2),
            "average": avg,
            "warning": None,
            "source": "direct_summary_average",
        }

    values = parse_training_xp_values_from_html(html)
    values = values[:60]
    count = len(values)
    total = round(sum(values), 2) if values else 0.0
    average = round(total / count, 2) if count else None
    warning = None
    if count and count < 60:
        warning = f"Fandt kun {count} plausible trænings-XP-tal; gennemsnit er beregnet over fundne værdier."
    if not count:
        warning = "Fandt ingen plausible trænings-XP-tal i HTML."
    return {
        "values": values,
        "count": count,
        "total": total,
        "average": average,
        "warning": warning,
        "source": "parsed_values",
    }

def _stat_search_terms() -> list[str]:
    terms = []
    for aliases in STAT_ALIASES.values():
        terms.extend(aliases)
    terms.extend(["Egenskaber", "Attributes", "Skills", "Total", "Rating", "Value", "Værdi"])
    return sorted(set(terms), key=len, reverse=True)


def _tag_debug_name(tag) -> str:
    parts = [str(getattr(tag, "name", "tag"))]
    attrs = getattr(tag, "attrs", {}) or {}
    if attrs.get("id"):
        parts.append(f"#{attrs.get('id')}")
    classes = attrs.get("class")
    if classes:
        try:
            parts.append("." + ".".join(str(c) for c in classes))
        except Exception:
            pass
    return "".join(parts)


def _text_contains_stat_term(text: str) -> bool:
    text_l = str(text or "").lower()
    return any(term.lower() in text_l for term in _stat_search_terms())


def _snippet_around(text: str, needle: str, radius: int = 420) -> str | None:
    m = re.search(re.escape(needle), text, re.IGNORECASE)
    if not m:
        return None
    start = max(0, m.start() - radius)
    end = min(len(text), m.end() + radius)
    return text[start:end].strip()


def build_player_debug_bundle(url: str, output_base: str | Path | None = None) -> Path:
    """Gem rå HTML og debug-filer for et VMAN-spillerlink."""
    try:
        import requests
    except Exception as error:
        raise RuntimeError("requests mangler. Installer fx med: pip install requests") from error

    player_id = extract_player_id(url)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    url, html = fetch_player_html_from_reference(url, session)
    training_url = None
    training_html = None
    training_error = None
    try:
        training_url, training_html = fetch_training_html_from_reference(url, session)
    except Exception as error:
        training_error = repr(error)

    soup = _soup_from_html(html)
    visible_text = html_to_visible_text(html)

    snap = None
    parser_error = None
    try:
        snap = parse_player_html(html, url)
    except Exception as error:
        parser_error = repr(error)

    if output_base is None:
        desktop = Path.home() / "Desktop"
        output_base = desktop if desktop.exists() else Path.cwd()
    else:
        output_base = Path(output_base)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_base) / f"VMAN_debug_player_{player_id}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "01_raw.html").write_text(html, encoding="utf-8", errors="replace")
    (out_dir / "02_visible_text.txt").write_text(visible_text, encoding="utf-8", errors="replace")

    training_summary = None
    if training_html is not None:
        (out_dir / "07_training_raw.html").write_text(training_html, encoding="utf-8", errors="replace")
        (out_dir / "08_training_visible_text.txt").write_text(html_to_visible_text(training_html), encoding="utf-8", errors="replace")
        try:
            training_summary = summarize_training_xp_from_html(training_html)
        except Exception as error:
            training_summary = {"parser_error": repr(error)}
        (out_dir / "09_training_xp_parser_result.json").write_text(
            json.dumps({"training_url": training_url, "training_error": training_error, "summary": training_summary}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            training_debug = debug_training_xp_candidates_from_html(training_html)
            (out_dir / "10_training_xp_candidates.json").write_text(
                json.dumps(training_debug, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (out_dir / "10_training_xp_candidates.txt").write_text(
                format_training_xp_debug_report(training_debug),
                encoding="utf-8",
                errors="replace",
            )
            (out_dir / "11_training_table_matrix.tsv").write_text(
                training_table_matrix_tsv_from_html(training_html),
                encoding="utf-8",
                errors="replace",
            )
            (out_dir / "12_training_numeric_candidates.tsv").write_text(
                training_numeric_candidates_tsv_from_html(training_html),
                encoding="utf-8",
                errors="replace",
            )
        except Exception as error:
            (out_dir / "10_training_xp_candidates.txt").write_text(
                f"Kunne ikke bygge træningskandidatoversigt: {error!r}",
                encoding="utf-8",
                errors="replace",
            )
    else:
        (out_dir / "09_training_xp_parser_result.json").write_text(
            json.dumps({"training_url": training_url, "training_error": training_error, "summary": None}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if snap is not None and isinstance(training_summary, dict):
        try:
            snap.training_url = training_url
            snap.training_xp_average = training_summary.get("average")
            snap.training_xp_total = training_summary.get("total")
            snap.training_xp_count = int(training_summary.get("count") or 0)
            snap.training_parse_warning = training_summary.get("warning")
        except Exception:
            pass

    snippets = []
    for term in _stat_search_terms():
        snip = _snippet_around(visible_text, term)
        if snip:
            snippets.append(f"\n\n===== {term} =====\n{snip}")
    (out_dir / "03_stat_snippets.txt").write_text(
        "".join(snippets).strip() or "Ingen statnavne fundet i synlig tekst.",
        encoding="utf-8",
        errors="replace",
    )

    element_lines = []
    seen = set()
    for tag in soup.find_all(True):
        txt = normalize_spaces(tag.get_text(" ", strip=True))
        if not txt or not _text_contains_stat_term(txt):
            continue
        if len(txt) > 2600:
            continue
        key = (tag.name, txt[:300])
        if key in seen:
            continue
        seen.add(key)
        attrs = getattr(tag, "attrs", {}) or {}
        outer = str(tag)
        if len(outer) > 4000:
            outer = outer[:4000] + "\n<!-- truncated -->"
        element_lines.append(
            f"\n\n===== {_tag_debug_name(tag)} =====\n"
            f"ATTRS: {json.dumps(attrs, ensure_ascii=False, default=str)}\n"
            f"TEXT: {txt}\n"
            f"HTML:\n{outer}"
        )
    (out_dir / "04_candidate_elements.html").write_text(
        "".join(element_lines).strip() or "Ingen kandidat-elementer fundet.",
        encoding="utf-8",
        errors="replace",
    )

    script_lines = []
    for index, script in enumerate(soup.find_all("script"), start=1):
        src = script.get("src", "")
        script_text = script.string or script.get_text("\n", strip=False) or ""
        relevant = _text_contains_stat_term(script_text) or any(k in script_text.lower() for k in ["player", "skill", "attribute", "rating", "value"])
        if src or relevant:
            preview = script_text.strip()
            if len(preview) > 6000:
                preview = preview[:6000] + "\n/* truncated */"
            script_lines.append(
                f"\n\n===== script {index} =====\n"
                f"SRC: {src}\n"
                f"RELEVANT: {relevant}\n"
                f"CONTENT:\n{preview}"
            )
    (out_dir / "05_script_json_candidates.txt").write_text(
        "".join(script_lines).strip() or "Ingen script-/JSON-kandidater fundet.",
        encoding="utf-8",
        errors="replace",
    )

    # 2.37: Særskilt avatar-debug. Her vil vi vide, om fejlen ligger i
    # HTML-fund, download/cookies/headers eller senere visning i Tk.
    avatar_debug: dict[str, object] = {
        "profile_url": url,
        "chosen_avatar_url": snap.avatar_url if snap is not None else None,
        "candidate_records": _avatar_candidate_records_from_html(html, url),
        "download_attempts": [],
    }
    seen_avatar_urls = set()
    avatar_candidate_urls: list[str] = []
    for rec in avatar_debug["candidate_records"]:  # type: ignore[index]
        if int(rec.get("score") or 0) <= 0:
            continue
        cand_url = str(rec.get("url") or "")
        if cand_url and cand_url not in seen_avatar_urls:
            seen_avatar_urls.add(cand_url)
            avatar_candidate_urls.append(cand_url)
    for index, cand_url in enumerate(avatar_candidate_urls[:6], start=1):
        attempt: dict[str, object] = {"index": index, "url": cand_url, "ok": False}
        try:
            headers = {
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "User-Agent": USER_AGENT,
                "Referer": url,
            }
            resp = session.get(cand_url, timeout=REQUEST_TIMEOUT, headers=headers)
            content = resp.content or b""
            content_type = resp.headers.get("Content-Type")
            attempt.update({
                "status_code": resp.status_code,
                "reason": getattr(resp, "reason", ""),
                "content_type": content_type,
                "bytes": len(content),
                "final_url": getattr(resp, "url", cand_url),
                "first_bytes_hex": content[:32].hex(),
            })
            resp.raise_for_status()
            suffix = _avatar_file_suffix(content, content_type, cand_url)
            saved_name = f"14_avatar_candidate_{index:02d}{suffix}"
            (out_dir / saved_name).write_bytes(content)
            attempt["saved_file"] = saved_name
            # Valider samme vej som den almindelige import gør.
            try:
                fetch_avatar_image_bytes(cand_url, session, referer=url)
                attempt["ok"] = True
            except Exception as validate_error:
                attempt["validation_error"] = repr(validate_error)
        except Exception as error:
            attempt["error"] = repr(error)
        avatar_debug["download_attempts"].append(attempt)  # type: ignore[index]
    (out_dir / "13_avatar_candidates.json").write_text(
        json.dumps(avatar_debug, ensure_ascii=False, indent=2),
        encoding="utf-8",
        errors="replace",
    )
    (out_dir / "13_avatar_candidates.txt").write_text(
        format_avatar_debug_report(avatar_debug),
        encoding="utf-8",
        errors="replace",
    )

    parsed = {
        "url": url,
        "player_id": player_id,
        "parser_error": parser_error,
        "parsed": None,
    }
    if snap is not None:
        parsed["parsed"] = {
            "navn": snap.navn,
            "alder": snap.alder,
            "vaerdi_mio": snap.vaerdi_mio,
            "rating": snap.rating,
            "position_code": snap.position_code,
            "engine_position": snap.engine_position,
            "stats": snap.stats or {},
            "training_xp_average": snap.training_xp_average,
            "training_xp_total": snap.training_xp_total,
            "training_xp_count": snap.training_xp_count,
            "training_url": snap.training_url,
            "training_parse_warning": snap.training_parse_warning,
            "avatar_url": snap.avatar_url,
            "avatar_content_type": snap.avatar_content_type,
            "avatar_image_bytes": len(snap.avatar_image_bytes or b""),
            "avatar_candidate_urls": _avatar_candidate_urls_from_html(html, url),
            "avatar_candidate_count": len(_avatar_candidate_urls_from_html(html, url)),
        }
    (out_dir / "06_parser_result.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    (out_dir / "README.txt").write_text(
        "Upload gerne hele denne mappe som ZIP i ChatGPT.\n\n"
        "Vigtigst:\n"
        "- 01_raw.html\n"
        "- 03_stat_snippets.txt\n"
        "- 04_candidate_elements.html\n"
        "- 05_script_json_candidates.txt\n"
        "- 06_parser_result.json\n"
        "- 07_training_raw.html / 09_training_xp_parser_result.json\n",
        encoding="utf-8",
    )
    return out_dir

def fetch_player_snapshot(url: str, player_name: str | None = None) -> PlayerLinkSnapshot:
    try:
        import requests
    except Exception as error:
        raise RuntimeError("requests mangler. Installer fx med: pip install requests") from error

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    profile_url, html = fetch_player_html_from_reference(url, session, player_name=player_name)
    snapshot = parse_player_html(html, profile_url)

    if snapshot.avatar_url:
        try:
            snapshot.avatar_image_bytes, snapshot.avatar_content_type = fetch_avatar_image_bytes(snapshot.avatar_url, session, referer=profile_url)
        except Exception:
            # Avatar må aldrig blokere almindelig spillerimport.
            snapshot.avatar_image_bytes = None
            snapshot.avatar_content_type = None

    try:
        training_url, training_html = fetch_training_html_from_reference(profile_url, session, player_name=player_name)
        summary = summarize_training_xp_from_html(training_html)
        snapshot.training_url = training_url
        snapshot.training_xp_average = summary.get("average")
        snapshot.training_xp_total = summary.get("total")
        snapshot.training_xp_count = int(summary.get("count") or 0)
        snapshot.training_parse_warning = summary.get("warning")
    except Exception as error:
        snapshot.training_parse_warning = f"Trænings-XP ikke hentet: {error}"

    return snapshot

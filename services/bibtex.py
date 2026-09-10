"""
BibTeX-Tools ohne externes Parsing-Package (reines Python).

Nutzung:
- Kurs-Quellen-Import:  POST /api/courses/{id}/references/import-bibtex (Einfügen)
- Zip-Import-Wizard:    deterministische Quellen-Detektion (services/import_service.py)

- parse_bibtex(text)      → [{entry_type, key, fields: {name: value}}]
- parse_bbl_keys(text)    → [key, ...]  (\\bibitem-Keys aus kompilierten .bbl-Dateien)
- entry_to_reference(e)   → Felder des Kurs-Quellen-Modells (Key-Set = CourseReferenceBase)
- split_authors(s)        → ["Last, First", "Last2, First2", ...]
- format_entry(...)       → eindeutige formatierte Bibliographie-Zeile (UI/Refmap/LLM)
"""

import re

# ─── BibTeX-Parser ────────────────────────────────────────────────────


def _is_quote(text: str, i: int) -> bool:
    # Anführungszeichen als String-Grenze? Nein, falls es backslash-escaped ist.
    return text[i] == '"' and (i == 0 or text[i - 1] != '\\')


def _read_balanced_braces(text: str, open_idx: int) -> tuple[int, int]:
    """Inner-Bereich (start, end) des durch text[open_idx] = '{' geöffneten Blocks.

    Berücksichtigt geschachtelte {} und "Strings" (in Strings hat {} keine Tiefe).
    Liefert (-1, -1), wenn kein schließendes '}' gefunden wird.
    """
    depth = 0
    in_str = False
    for i in range(open_idx, len(text)):
        c = text[i]
        if in_str:
            if _is_quote(text, i):
                in_str = False
        elif _is_quote(text, i):
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return open_idx + 1, i
    return -1, -1


def _split_top_level(body: str, sep: str = ",") -> list[str]:
    """Teilt an sep auf, aber nur auf geschweifter Tiefe 0 und außerhalb von "Strings".

    Dadurch überleben Kommas IN Werten (z.B. publisher = {Verlag, Stadt}).
    """
    parts: list[str] = []
    depth = 0
    in_str = False
    cur: list[str] = []
    for i, c in enumerate(body):
        if in_str:
            cur.append(c)
            if _is_quote(body, i):
                in_str = False
            continue
        if _is_quote(body, i):
            in_str = True
            cur.append(c)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


_LATEX_UMAUT = {"a": "ä", "o": "ö", "u": "ü", "A": "Ä", "O": "Ö", "U": "Ü",
                "e": "ë", "E": "Ë", "i": "ï", "I": "Ï", "y": "ÿ"}
_LATEX_AKU = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú",
              "A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú"}
_LATEX_GRAV = {"a": "à", "e": "è", "i": "ì", "o": "ò", "u": "ù",
               "A": "À", "E": "È", "I": "Ì", "O": "Ò", "U": "Ù"}
_LATEX_TILDE = {"n": "ñ", "N": "Ñ", "a": "ã", "A": "Ã", "o": "õ", "O": "Õ"}


def _clean_field_value(v: str) -> str:
    """Entfernt äußere {} / "..." und entschlüsselt BibTeX-Escapes (best effort)."""
    v = v.strip()
    if v.startswith("{") and v.endswith("}"):
        depth = 0
        balanced = True
        for i, c in enumerate(v):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            if depth == 0 and i < len(v) - 1:
                balanced = False
                break
        if balanced:
            v = v[1:-1].strip()
    elif v.startswith('"') and v.endswith('"'):
        v = v[1:-1]
    # LaTeX-Akzente → Unicode (\"o → ö, \'e → é, `a → à, \~n → ñ, \ss → ß)
    v = re.sub(r'\\"([A-Za-z])', lambda m: _LATEX_UMAUT.get(m.group(1), m.group(1)), v)
    v = re.sub(r"\\'([A-Za-z])", lambda m: _LATEX_AKU.get(m.group(1), m.group(1)), v)
    v = re.sub(r"`([A-Za-z])", lambda m: _LATEX_GRAV.get(m.group(1), m.group(1)), v)
    v = re.sub(r"\\~([A-Za-z])", lambda m: _LATEX_TILDE.get(m.group(1), m.group(1)), v)
    v = v.replace("\\ss", "ß")
    # Verbleibende Escapes: \" → "  \& → &  \\ → \  übrige \cmd → cmd
    v = v.replace('\\"', '"').replace("\\&", "&").replace("\\\\", "\\")
    v = re.sub(r"\\([a-zA-Z]+)", r"\1", v)
    v = v.replace("~", " ")
    return re.sub(r"\s+", " ", v).strip()


def parse_bibtex(text: str) -> list[dict]:
    """Parst BibTeX-Entries aus `text`.

    Returns: [{entry_type, key, fields: {name: value}}] in Vorkommensreihenfolge.
    Unerkannte/defekte Entries werden still übersprungen; @comment/@preamble/
    @string werden ignoriert.
    """
    entries: list[dict] = []
    pos = 0
    n = len(text or "")
    while pos < n:
        m = re.search(r"@([A-Za-z]+)\s*\{", text[pos:])
        if not m:
            break
        open_idx = pos + m.end() - 1
        inner_start, inner_end = _read_balanced_braces(text, open_idx)
        if inner_end < 0:
            break
        body = text[inner_start:inner_end]
        entry_type = m.group(1).lower()
        pos = inner_end + 1
        if entry_type in ("comment", "preamble", "string"):
            continue
        parts = _split_top_level(body)
        if not parts:
            continue
        key = _clean_field_value(parts[0])
        fields: dict[str, str] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            name = name.strip().lower()
            if not name or not re.fullmatch(r"[a-z0-9_-]+", name):
                continue
            fields[name] = _clean_field_value(value)
        if key:
            entries.append({"entry_type": entry_type, "key": key, "fields": fields})
    return entries


_BIBITEM_RE = re.compile(r"\\bibitem(?:\*|\[\d+\])?\s*\{([^{}]*)\}")


def parse_bbl_keys(text: str) -> list[str]:
    """\\bibitem-Keys aus einer kompilierten .bbl-Datei (in Reihenfolge)."""
    out: list[str] = []
    for m in _BIBITEM_RE.finditer(text or ""):
        key = m.group(1).strip()
        if key and key not in out:
            out.append(key)
    return out


# ─── Autoren ──────────────────────────────────────────────────────────


def split_authors(value: str) -> list[str]:
    """'Last, First and Last2, First2' → ['Last, First', 'Last2, First2'].

    Der Trenner ist ' and ' (BibTeX-Konvention); ein Komma in 'Last, First'
    gehört zum Namen und trennt keine Autoren.
    """
    v = (value or "").strip()
    if not v:
        return []
    parts = [p.strip() for p in re.split(r"\s+and\s+", v) if p.strip()]
    if len(parts) <= 1:
        parts = [v]
    return parts



# ─── Entry → Modell-Felder ────────────────────────────────────────────


def entry_to_reference(entry: dict) -> dict:
    """BibTeX-Entry → Quellen-Felder (Key-Set = CourseReferenceBase, ohne key/order)."""
    f = entry.get("fields") or {}
    vol = (f.get("volume") or "").strip()
    num = (f.get("number") or "").strip()
    pages = (f.get("pages") or "").strip()
    edition = (f.get("edition") or "").strip()
    parts: list[str] = []
    if vol:
        parts.append(f"{vol}({num})" if num else vol)
    elif num:
        parts.append(num)
    if pages:
        parts.append(pages)
    if edition:
        parts.append(edition)
    venue = (
        f.get("journal") or f.get("booktitle") or f.get("publisher") or f.get("school") or ""
    ).strip()
    return {
        "entry_type": entry.get("entry_type") or "misc",
        "authors": split_authors(f.get("author") or ""),
        "title": (f.get("title") or "").strip(),
        "year": (f.get("year") or "").strip(),
        "venue": venue,
        "detail": ", ".join(parts),
        "address": (f.get("address") or "").strip(),
        "doi": (f.get("doi") or "").strip(),
        "url": (f.get("url") or "").strip(),
        "note": (f.get("note") or "").strip(),
    }


# ─── Formatierung (eindeutige Regel: UI, Refmap, LLM-Prompts) ────────


def format_entry(
    authors: list[str],
    title: str,
    year: str,
    venue: str = "",
    detail: str = "",
    doi: str = "",
    url: str = "",
) -> str:
    """'A & B (2020). Titel. Venue, Detail. https://doi.org/...' (leere Felde weggelassen)."""
    s = " & ".join(authors or [])
    if year:
        s += f" ({year})"
    if s:
        s += ". "
    s += title or ""
    if s and not s.endswith((".", "!", "?")):
        s += "."
    tail = ", ".join(x for x in [venue or "", detail or ""] if x)
    if tail:
        s = (s + " " if s else "") + tail
        if not s.endswith((".", "!", "?")):
            s += "."
    link = (doi or url or "").strip()
    if link:
        if not link.startswith("http"):
            link = f"https://doi.org/{link}" if doi else link
        s = (s + " " if s else "") + link
    return s.strip()

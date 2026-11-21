"""Deterministic test-data generation (valid data for normal users, chaos data for gremlins)."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from ghost_twin.models import InputInfo

FIRST = ["Ana", "Luis", "Sofía", "Mateo", "Valentina", "Diego", "Camila", "Jorge", "Lucía",
         "Daniel", "Emma", "Noah", "Olivia", "Liam", "Mia", "Lucas"]
LAST = ["García", "Martínez", "López", "Rodríguez", "Pérez", "Gómez", "Smith", "Johnson",
        "Brown", "Silva", "Torres", "Díaz", "Romero", "Navarro"]
CITIES = ["Bogotá", "Madrid", "Lima", "Ciudad de México", "Buenos Aires", "Santiago", "Austin"]
COUNTRIES = ["Colombia", "Spain", "Peru", "Mexico", "Argentina", "Chile", "United States"]
WORDS = ["shoes", "laptop", "coffee", "backpack", "headphones", "jacket", "book", "lamp"]
SENTENCES = [
    "Hi! I'd like to know more about your product, especially the pricing for small teams.",
    "Hola, quisiera saber si hacen envíos internacionales y cuánto tardan.",
    "The checkout page didn't load my cart yesterday, could you take a look?",
    "Great service, just wanted to say thanks to the support team.",
]


@dataclass
class Identity:
    first: str
    last: str
    email: str
    username: str
    phone: str
    city: str
    country: str
    zip: str
    age: int
    birthdate: str

    @classmethod
    def generate(cls, rng: random.Random) -> Identity:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        n = rng.randint(10, 9999)
        ascii_first = first.lower().translate(str.maketrans("áéíóúñ", "aeioun"))
        ascii_last = last.lower().translate(str.maketrans("áéíóúñ", "aeioun"))
        age = rng.randint(19, 70)
        return cls(
            first=first, last=last,
            email=f"{ascii_first}.{ascii_last}{n}@example.com",
            username=f"{ascii_first}{n}",
            phone=f"+1 555 {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
            city=rng.choice(CITIES), country=rng.choice(COUNTRIES),
            zip=f"{rng.randint(10000, 99999)}", age=age,
            birthdate=f"{2026 - age}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        )


def _has(hint: str, *words: str) -> bool:
    """Substring match; short tokens (<=3 chars, e.g. ``age``/``tel``) need word boundaries."""
    for w in words:
        if len(w) <= 3:
            if re.search(rf"(?<![a-záéíóúñ]){re.escape(w)}(?![a-záéíóúñ])", hint):
                return True
        elif w in hint:
            return True
    return False


def _number_in_range(inp: InputInfo, rng: random.Random, default: tuple[int, int]) -> str:
    lo, hi = default
    try:
        if inp.min not in (None, ""):
            lo = int(float(inp.min))
        if inp.max not in (None, ""):
            hi = int(float(inp.max))
    except ValueError:
        pass
    if hi < lo:
        hi = lo
    return str(rng.randint(lo, hi))


def valid_value(inp: InputInfo, rng: random.Random, who: Identity) -> str:
    """A realistic, valid value for the input."""
    t, h = (inp.type or "text").lower(), inp.hint
    if inp.tag == "select":
        opts = [o for o in inp.options if o]
        return rng.choice(opts) if opts else ""
    if t in {"checkbox", "radio"}:
        return "on"
    if t == "email" or _has(h, "email", "correo", "e-mail"):
        return who.email
    if t == "password" or _has(h, "password", "contraseña", "passwd"):
        return "Gh0st-Twin!2026"
    if t == "tel" or _has(h, "phone", "tel", "móvil", "movil", "celular", "mobile"):
        return who.phone
    if t == "url" or _has(h, "website", "url", "sitio"):
        return "https://example.com"
    if t == "date" or _has(h, "birth", "nacimiento", "dob"):
        return who.birthdate if t in {"date", "text", ""} else "1990-05-17"
    if t == "datetime-local":
        return "2026-12-01T10:30"
    if t == "time":
        return "14:30"
    if t == "month":
        return "2026-12"
    if t == "week":
        return "2026-W49"
    if t == "color":
        return "#3366cc"
    if t in {"number", "range"}:
        if _has(h, "age", "edad"):
            return _number_in_range(inp, rng, (18, 80)) if inp.min is None else _number_in_range(
                inp, rng, (who.age, who.age))
        if _has(h, "qty", "quantity", "cantidad"):
            return _number_in_range(inp, rng, (1, 5))
        return _number_in_range(inp, rng, (1, 100))
    if _has(h, "first", "nombre", "given"):
        return who.first
    if _has(h, "last", "apellido", "surname", "family"):
        return who.last
    if _has(h, "user", "login", "usuario", "handle"):
        return who.username
    if _has(h, "name", "full"):
        return f"{who.first} {who.last}"
    if _has(h, "city", "ciudad"):
        return who.city
    if _has(h, "country", "país", "pais"):
        return who.country
    if _has(h, "zip", "postal", "postcode"):
        return who.zip
    if _has(h, "address", "dirección", "direccion", "street", "calle"):
        return f"{rng.randint(1, 999)} Main Street"
    if _has(h, "company", "empresa", "organization"):
        return "Acme Labs"
    if _has(h, "card", "tarjeta"):
        return "4242 4242 4242 4242"
    if _has(h, "cvv", "cvc"):
        return "123"
    if _has(h, "age", "edad"):
        return str(who.age)
    if _has(h, "search", "buscar", "query") or inp.name in {"q", "s", "query", "term"}:
        return rng.choice(WORDS)
    if inp.tag == "textarea" or _has(h, "message", "mensaje", "comment", "comentario",
                                     "description", "descripción", "bio", "feedback"):
        return rng.choice(SENTENCES)
    if _has(h, "subject", "asunto", "title", "título"):
        return "Question about my order"
    if _has(h, "code", "código", "coupon", "cupón"):
        return "WELCOME10"
    return rng.choice(WORDS).capitalize()


# --------------------------------------------------------------------------- chaos data

HUGE_LEN = 20_000
CHAOS_STRATEGIES = ["empty", "huge", "unicode", "type_confusion", "whitespace", "mixed"]


def chaos_value(inp: InputInfo, rng: random.Random, strategy: str) -> str:
    t = (inp.type or "text").lower()
    if strategy == "mixed":
        strategy = rng.choice(CHAOS_STRATEGIES[:-1])
    if strategy == "empty":
        return ""
    if strategy == "huge":
        return ("A" * 1000 + "🔥") * (HUGE_LEN // 1001)
    if strategy == "unicode":
        return rng.choice([
            "𝕳𝖊𝖑𝖑𝖔 漢字 مرحبا ‮RTL‬ 🙃",
            "Zalgo: Z̵̡̛a̷͎̒l̶̤̈́g̸̝͝o̵̬͗",
            "Ünïcödé — “quotes” ‘single’ …",
            "\u0000​﻿ zero-width",
        ])
    if strategy == "whitespace":
        return rng.choice(["   ", "\t\n", " leading and trailing  "])
    # type confusion
    if t in {"number", "range"}:
        return rng.choice(["abc", "-1", "1e309", "99999999999999999999999", "0x1F", "NaN"])
    if t == "email":
        return rng.choice(["not-an-email", "a@b", "@@", "user@" + "x" * 300 + ".com"])
    if t in {"date", "datetime-local"}:
        return rng.choice(["2026-02-30", "0000-00-00", "yesterday"])
    return rng.choice(["null", "undefined", "NaN", "-1", "[object Object]", "{}", "true",
                       "%s%s%s%n", "${7*7}", "{{7*7}}"])

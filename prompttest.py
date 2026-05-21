"""
Options:
    --soft-power   0.0–1.0   0=highest soft power countries, 1=lowest (default: 0.5)
    --overton      0.0–1.0   0=policy/mainstream, 1=unthinkable/radical (default: 0.3)
    --semantic     0.0–1.0   image conceptual distance from topic (default: 0.4)
    --filter       1|2|3     content filter: 1=U/PG, 2=15, 3=18 (default: 2)
    --state        depressed|stressed|excited|focused  (default: stressed)
    --country      override country selection (e.g. "Japan")
    --topic        override topic selection (e.g. "Folklore and mythology")
    --no-print     skip printing (text output only)
    --no-image     skip Wikimedia image fetch
    --dry-run      show config and exit without running

Example:
    python3 prompttest_standalone.py --overton 0.9 --filter 3 --state excited
"""

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import textwrap
import threading
import time
from typing import Dict, List
from urllib.parse import urlparse

import requests
from mistralai.client import Mistral
from collections import deque
_used_image_titles = deque(maxlen=20)

try:
    import qrcode
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ── CONFIG ────────────────────────────────────────────────────────────────────

MISTRAL_API_KEY = "QEjxW7dOoDcSu7dvtgXldxNMrILiEstq"         
PRINTER_PORT    = "/dev/ttyUSB0"
WIKIPEDIA_API   = "https://en.wikipedia.org/api/rest_v1/page/summary"
UNSPLASH_ACCESS_KEY = "SctRjEvp8rYOno8CZhaixfMCm4kTiO23GdoYWrI-RPg"
OPEN_NOTEBOOK_API = "http://localhost:5055"
MAX_FINAL_SOURCES = 5
INDEX_CSV_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soft_power_index.csv")

SOFT_POWER_TEMPERATURE = 0.08

client = Mistral(api_key=MISTRAL_API_KEY, timeout_ms=60000)

# ── STATE (set from CLI args in main) ─────────────────────────────────────────

CONTENT_FILTER  = 2
EMOTIONAL_STATE = "stressed"

_notebook_id = None

# ── TOPICS ────────────────────────────────────────────────────────────────────

TOPICS = [
    # ── CULTURE & ARTS ──────────────────────────────────────────────
    {"topic": "Folklore and mythology",              "overton": 0.2, "energy": ["depressed", "excited"]},
    {"topic": "Proverbs and folk wisdom",            "overton": 0.1, "energy": ["depressed", "stressed"]},
    {"topic": "Music and song traditions",           "overton": 0.2, "energy": ["depressed", "excited"]},
    {"topic": "Dance and movement traditions",       "overton": 0.2, "energy": ["excited", "focused"]},
    {"topic": "Visual arts and sculpture",           "overton": 0.2, "energy": ["depressed", "excited"]},
    {"topic": "Street art and graffiti culture",     "overton": 0.5, "energy": ["excited", "stressed"]},
    {"topic": "Cuisine and food traditions",         "overton": 0.2, "energy": ["depressed", "excited"]},
    {"topic": "Clothing and traditional dress",      "overton": 0.2, "energy": ["depressed", "focused"]},
    {"topic": "Language and dialects",               "overton": 0.2, "energy": ["focused", "excited"]},
    {"topic": "Humour and comedy traditions",        "overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Architecture and built environment",  "overton": 0.2, "energy": ["focused", "excited"]},
    {"topic": "Film and cinema culture",             "overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Theatre and performance traditions",  "overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Literature and oral storytelling",    "overton": 0.2, "energy": ["depressed", "focused"]},
    {"topic": "Poetry and spoken word",              "overton": 0.3, "energy": ["depressed", "excited"]},
    {"topic": "Graphic novels and comics culture",   "overton": 0.4, "energy": ["excited", "focused"]},
    {"topic": "Underground and DIY music scenes",    "overton": 0.6, "energy": ["excited", "stressed"]},
    {"topic": "Video game culture and development",  "overton": 0.4, "energy": ["excited", "focused"]},
    {"topic": "Fashion subcultures",                 "overton": 0.5, "energy": ["excited", "stressed"]},
    {"topic": "Tattoo and body modification culture","overton": 0.5, "energy": ["excited", "depressed"]},
    {"topic": "Craft and artisan traditions",        "overton": 0.2, "energy": ["depressed", "focused"]},
    {"topic": "Photography and documentary culture", "overton": 0.4, "energy": ["focused", "excited"]},
    {"topic": "Animation and illustration culture",  "overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Radio and broadcast culture",         "overton": 0.3, "energy": ["focused", "depressed"]},

    # ── INDIVIDUALS & FIGURES ────────────────────────────────────────
    {"topic": "Obscure but influential filmmakers",          "overton": 0.5, "energy": ["excited", "focused"]},
    {"topic": "Underground or banned writers and poets",     "overton": 0.6, "energy": ["excited", "stressed"]},
    {"topic": "Forgotten women pioneers",                    "overton": 0.5, "energy": ["focused", "excited"]},
    {"topic": "Radical or dissident intellectuals",          "overton": 0.7, "energy": ["excited", "stressed"]},
    {"topic": "Outsider and self-taught artists",            "overton": 0.5, "energy": ["depressed", "excited"]},
    {"topic": "Niche or cult musicians",                     "overton": 0.5, "energy": ["excited", "depressed"]},
    {"topic": "Revolutionary or resistance leaders",         "overton": 0.6, "energy": ["excited", "stressed"]},
    {"topic": "Local heroes and unsung figures",             "overton": 0.4, "energy": ["depressed", "focused"]},
    {"topic": "Controversial scientists or thinkers",        "overton": 0.6, "energy": ["focused", "excited"]},
    {"topic": "Architects who changed a city",               "overton": 0.4, "energy": ["focused", "excited"]},
    {"topic": "Pioneering chefs and food revolutionaries",   "overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Activist photographers and documentarians",   "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Eccentric collectors and obsessives",         "overton": 0.4, "energy": ["excited", "depressed"]},
    {"topic": "Cult TV directors and showrunners",           "overton": 0.4, "energy": ["excited", "focused"]},
    {"topic": "Philosophers who died in obscurity",          "overton": 0.5, "energy": ["depressed", "focused"]},

    # ── FILM & MEDIA (SPECIFIC) ──────────────────────────────────────
    {"topic": "Cult films popular in one country only",      "overton": 0.5, "energy": ["excited", "depressed"]},
    {"topic": "National cinema movements",                   "overton": 0.4, "energy": ["excited", "focused"]},
    {"topic": "Banned or censored films",                    "overton": 0.6, "energy": ["excited", "stressed"]},
    {"topic": "Documentary filmmaking traditions",           "overton": 0.4, "energy": ["focused", "stressed"]},
    {"topic": "Propaganda films and state cinema",           "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Horror film traditions and folklore",         "overton": 0.5, "energy": ["excited", "stressed"]},
    {"topic": "TV shows that defined a generation",          "overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Pirate radio and illegal broadcasting",       "overton": 0.7, "energy": ["excited", "stressed"]},
    {"topic": "Social media and influencer culture",         "overton": 0.4, "energy": ["focused", "stressed"]},

    # ── BELIEF & PHILOSOPHY ──────────────────────────────────────────
    {"topic": "Religion and spirituality",           "overton": 0.2, "energy": ["depressed", "focused"]},
    {"topic": "Philosophy and ethics",               "overton": 0.3, "energy": ["focused", "stressed"]},
    {"topic": "Ancient philosophy",                  "overton": 0.2, "energy": ["focused", "depressed"]},
    {"topic": "Political philosophy",                "overton": 0.4, "energy": ["focused", "stressed"]},
    {"topic": "Epistemology and ways of knowing",    "overton": 0.4, "energy": ["focused", "excited"]},
    {"topic": "Mysticism and esoteric traditions",   "overton": 0.5, "energy": ["excited", "depressed"]},
    {"topic": "Atheism and secularism",              "overton": 0.4, "energy": ["focused", "stressed"]},
    {"topic": "New religious movements",             "overton": 0.6, "energy": ["excited", "stressed"]},
    {"topic": "Animism and indigenous belief",       "overton": 0.4, "energy": ["depressed", "excited"]},
    {"topic": "Death cults and apocalyptic movements","overton": 0.7, "energy": ["excited", "stressed"]},
    {"topic": "Superstitions and taboos",            "overton": 0.3, "energy": ["excited", "depressed"]},

    # ── POLITICS & POWER ─────────────────────────────────────────────
    {"topic": "Politics and government",             "overton": 0.3, "energy": ["focused", "stressed"]},
    {"topic": "National identity and independence",  "overton": 0.3, "energy": ["focused", "excited"]},
    {"topic": "Colonial history",                    "overton": 0.4, "energy": ["focused", "stressed"]},
    {"topic": "Borders and territorial disputes",    "overton": 0.4, "energy": ["focused", "stressed"]},
    {"topic": "Corruption and kleptocracy",          "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Censorship and propaganda",           "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Surveillance and control",            "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Political imprisonment",              "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Resistance and liberation movements", "overton": 0.6, "energy": ["excited", "stressed"]},
    {"topic": "Anarchism and anti-state movements",  "overton": 0.7, "energy": ["excited", "stressed"]},
    {"topic": "Electoral fraud and stolen elections","overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Legal systems and justice",           "overton": 0.3, "energy": ["focused", "stressed"]},
    {"topic": "Police brutality and state violence", "overton": 0.7, "energy": ["focused", "stressed"]},
    {"topic": "Tax havens and financial secrecy",    "overton": 0.6, "energy": ["focused", "stressed"]},

    # ── HISTORY & CONFLICT ───────────────────────────────────────────
    {"topic": "Military history",                    "overton": 0.3, "energy": ["focused", "stressed"]},
    {"topic": "Genocide and mass atrocity",          "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Slavery and forced labour",           "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Torture and punishment",              "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Famine and starvation",               "overton": 0.5, "energy": ["focused", "depressed"]},
    {"topic": "Nuclear warfare and testing",         "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Biological and chemical weapons",     "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Ethnic cleansing",                    "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Child soldiers and youth in conflict","overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "War crimes and tribunals",            "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Colonialism and its afterlives",      "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Cold War proxy conflicts",            "overton": 0.5, "energy": ["focused", "excited"]},

    # ── SOCIETY & IDENTITY ───────────────────────────────────────────
    {"topic": "Gender roles and traditions",         "overton": 0.3, "energy": ["focused", "stressed"]},
    {"topic": "Coming-of-age rituals",               "overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Death and burial customs",            "overton": 0.3, "energy": ["depressed", "focused"]},
    {"topic": "Social inequality and class",         "overton": 0.4, "energy": ["focused", "stressed"]},
    {"topic": "Immigration and diaspora",            "overton": 0.4, "energy": ["focused", "depressed"]},
    {"topic": "Sexuality and queer history",         "overton": 0.5, "energy": ["excited", "focused"]},
    {"topic": "Disability and its cultural history", "overton": 0.4, "energy": ["depressed", "focused"]},
    {"topic": "Childhood and education systems",     "overton": 0.3, "energy": ["focused", "depressed"]},
    {"topic": "Ageing and elder culture",            "overton": 0.3, "energy": ["depressed", "focused"]},
    {"topic": "Marriage and kinship systems",        "overton": 0.3, "energy": ["focused", "depressed"]},
    {"topic": "Forced marriage and honour violence", "overton": 0.7, "energy": ["focused", "stressed"]},
    {"topic": "Human trafficking",                   "overton": 0.7, "energy": ["focused", "stressed"]},
    {"topic": "Sex work and exploitation",           "overton": 0.7, "energy": ["focused", "stressed"]},
    {"topic": "Racism and racial caste systems",     "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Memory and national commemoration",   "overton": 0.4, "energy": ["depressed", "focused"]},
    {"topic": "Protest and civil disobedience",      "overton": 0.5, "energy": ["excited", "stressed"]},

    # ── HEALTH & THE BODY ────────────────────────────────────────────
    {"topic": "Traditional medicine and healing",    "overton": 0.3, "energy": ["depressed", "focused"]},
    {"topic": "Health and disease history",          "overton": 0.3, "energy": ["focused", "depressed"]},
    {"topic": "Mental health and its treatment",     "overton": 0.4, "energy": ["depressed", "focused"]},
    {"topic": "Addiction and substance abuse",       "overton": 0.6, "energy": ["depressed", "stressed"]},
    {"topic": "Suicide and self-destruction",        "overton": 0.7, "energy": ["depressed", "stressed"]},
    {"topic": "Human experimentation",               "overton": 0.7, "energy": ["focused", "stressed"]},
    {"topic": "Child mortality and infanticide",     "overton": 0.7, "energy": ["focused", "depressed"]},
    {"topic": "Eugenics and population control",     "overton": 0.7, "energy": ["focused", "stressed"]},
    {"topic": "Psychedelic and altered states",      "overton": 0.6, "energy": ["excited", "depressed"]},
    {"topic": "Body image and beauty standards",     "overton": 0.4, "energy": ["focused", "stressed"]},

    # ── SCIENCE, NATURE & ENVIRONMENT ───────────────────────────────
    {"topic": "Astronomy and cosmology",             "overton": 0.2, "energy": ["excited", "depressed"]},
    {"topic": "Mathematics and numeracy",            "overton": 0.2, "energy": ["focused", "excited"]},
    {"topic": "Natural history and ecology",         "overton": 0.2, "energy": ["excited", "depressed"]},
    {"topic": "Modern scientific contributions",     "overton": 0.3, "energy": ["focused", "excited"]},
    {"topic": "Environmental challenges",            "overton": 0.4, "energy": ["focused", "stressed"]},
    {"topic": "Environmental catastrophe",           "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Animal cruelty and extinction",       "overton": 0.5, "energy": ["depressed", "stressed"]},
    {"topic": "Nuclear disasters and fallout",       "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Water scarcity and conflict",         "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Rewilding and conservation",          "overton": 0.4, "energy": ["excited", "focused"]},
    {"topic": "Indigenous land rights",              "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Deep sea and unexplored environments","overton": 0.3, "energy": ["excited", "depressed"]},
    {"topic": "Space exploration history",           "overton": 0.3, "energy": ["excited", "focused"]},

    # ── ECONOMICS & LABOUR ───────────────────────────────────────────
    {"topic": "Labour movements and strikes",        "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Debt and financial crisis",           "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Informal and shadow economies",       "overton": 0.5, "energy": ["focused", "excited"]},
    {"topic": "Land ownership and dispossession",    "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Food sovereignty and hunger",         "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Cooperative and commons economies",   "overton": 0.5, "energy": ["excited", "focused"]},
    {"topic": "Child labour and exploitation",       "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Gig economy and precarious work",     "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Organised crime and cartels",         "overton": 0.7, "energy": ["focused", "excited"]},

    # ── TECHNOLOGY & MEDIA ───────────────────────────────────────────
    {"topic": "Internet culture and subcultures",    "overton": 0.4, "energy": ["excited", "focused"]},
    {"topic": "Surveillance capitalism",             "overton": 0.6, "energy": ["focused", "stressed"]},
    {"topic": "Disinformation and fake news",        "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Hacking and cypherpunk culture",      "overton": 0.7, "energy": ["excited", "focused"]},
    {"topic": "AI and automation anxiety",           "overton": 0.5, "energy": ["focused", "stressed"]},
    {"topic": "Extremism and radicalisation online", "overton": 0.7, "energy": ["focused", "stressed"]},
    {"topic": "Cryptocurrency and financial dissent","overton": 0.6, "energy": ["excited", "focused"]},
    {"topic": "Industrial and technological development","overton": 0.3, "energy": ["focused", "excited"]},
    {"topic": "Maritime and navigation history",     "overton": 0.3, "energy": ["excited", "focused"]},
    {"topic": "Weapons and warfare technology",      "overton": 0.4, "energy": ["focused", "excited"]},

    # ── EXTREME / TABOO (0.8–0.95) ───────────────────────────────────
    {"topic": "Snuff and death tourism",                     "overton": 0.85, "energy": ["excited", "stressed"]},
    {"topic": "Cannibal cults and ritual consumption",       "overton": 0.90, "energy": ["excited", "stressed"]},
    {"topic": "State-sanctioned rape as a weapon of war",    "overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Child marriage and its legal protection",     "overton": 0.80, "energy": ["focused", "stressed"]},
    {"topic": "Organ harvesting and black market medicine",  "overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Suicide cults and mass death events",         "overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Underground abortion networks",               "overton": 0.80, "energy": ["focused", "stressed"]},
    {"topic": "Necropolitics and who is allowed to die",     "overton": 0.80, "energy": ["focused", "stressed"]},
    {"topic": "Feral children and social isolation cases",   "overton": 0.80, "energy": ["excited", "depressed"]},
    {"topic": "Coercive psychiatry and political diagnosis", "overton": 0.80, "energy": ["focused", "stressed"]},
    {"topic": "Debt bondage and modern slavery systems",     "overton": 0.80, "energy": ["focused", "stressed"]},
    {"topic": "Forced sterilisation programmes",             "overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Genocide denial and its legal status",        "overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Chemical castration as punishment",           "overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Extremist recruitment and indoctrination",    "overton": 0.80, "energy": ["focused", "stressed"]},
    {"topic": "Darknet economies and anonymous markets",     "overton": 0.90, "energy": ["focused", "excited"]},
    {"topic": "Assassination programmes and state killings", "overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Psychological torture and sensory deprivation","overton": 0.85, "energy": ["focused", "stressed"]},
    {"topic": "Paedophile networks and institutional cover", "overton": 0.90, "energy": ["focused", "stressed"]},
    {"topic": "Arms dealing and illegal weapons markets",    "overton": 0.80, "energy": ["focused", "excited"]},
    {"topic": "Viral snuff and trauma content online",       "overton": 0.95, "energy": ["focused", "stressed"]},
    {"topic": "Execution tourism and public death spectacle","overton": 0.90, "energy": ["focused", "excited"]},
]

# Topics that are never appropriate for U/PG (filter=1)
UPGO_BLOCKED_TOPICS = {
    "Snuff and death tourism",
    "Cannibal cults and ritual consumption",
    "State-sanctioned rape as a weapon of war",
    "Child marriage and its legal protection",
    "Organ harvesting and black market medicine",
    "Suicide cults and mass death events",
    "Underground abortion networks",
    "Feral children and social isolation cases",
    "Coercive psychiatry and political diagnosis",
    "Debt bondage and modern slavery systems",
    "Forced sterilisation programmes",
    "Genocide denial and its legal status",
    "Chemical castration as punishment",
    "Extremist recruitment and indoctrination",
    "Darknet economies and anonymous markets",
    "Assassination programmes and state killings",
    "Psychological torture and sensory deprivation",
    "Paedophile networks and institutional cover",
    "Arms dealing and illegal weapons markets",
    "Viral snuff and trauma content online",
    "Execution tourism and public death spectacle",
    "Necropolitics and who is allowed to die",
    "Genocide and mass atrocity",
    "Slavery and forced labour",
    "Torture and punishment",
    "Child mortality and infanticide",
    "Human experimentation",
    "Sex work and exploitation",
    "Suicide and self-destruction",
    "Biological and chemical weapons",
    "Ethnic cleansing",
    "Child soldiers and youth in conflict",
    "Forced marriage and honour violence",
    "Human trafficking",
    "Police brutality and state violence",
    "Addiction and substance abuse",
    "Nuclear warfare and testing",
}

# Topics that are never appropriate for filter=1 or filter=2
FILTER_2_BLOCKED_TOPICS = {
    "Snuff and death tourism",
    "Cannibal cults and ritual consumption",
    "Paedophile networks and institutional cover",
    "Viral snuff and trauma content online",
    "Execution tourism and public death spectacle",
    "Darknet economies and anonymous markets",
}

def pick_topic(overton_dial: float, emotional_state: str, content_filter: int = 2) -> str:
    weights = []
    candidates = []
    for t in TOPICS:
        topic_name = t["topic"]

        # hard block by content filter
        if content_filter == 1 and topic_name in UPGO_BLOCKED_TOPICS:
            continue
        if content_filter <= 2 and topic_name in FILTER_2_BLOCKED_TOPICS:
            continue

        overton_distance = abs(t["overton"] - overton_dial)
        overton_weight   = 1.0 - overton_distance
        energy_weight    = 2.0 if emotional_state in t["energy"] else 0.5

        candidates.append(t)
        weights.append(overton_weight * energy_weight)

    chosen = random.choices(candidates, weights=weights, k=1)[0]
    return chosen["topic"]

# ── CONTENT FILTER ────────────────────────────────────────────────────────────

CONTENT_FILTER_SYSTEM = {
    1: (
        "You are writing for a curious child. Use warm, clear, simple language. "
        "Every sentence should feel like a discovery, not a warning."
    ),
    2: (
        "You are an intelligent broadsheet journalist. You don't soften things. "
        "You choose the most interesting, honest angle, even if it's uncomfortable."
    ),
    3: (
        "You have no editorial filter. You choose the most confronting, taboo, or "
        "unsettling true fact available. Do not soften. Do not qualify."
    ),
}

CONTENT_FILTER_GUIDANCE = {
    1: (
        "U/PG (Universal/Parental Guidance) — suitable for all ages. "
        "Sources and angles must be appropriate for children and families. "
        "Mild conflict, historical events, and social issues are acceptable if handled "
        "with sensitivity and care. Avoid anything frightening, disturbing, or distressing. "
        "Focus on discovery, culture, human achievement, and the world presented with curiosity."
    ),
    2: (
        "15 — suitable for ages 15 and over, following BBFC guidelines. "
        "Sources and angles may include strong language, drug use, violence without excessive "
        "detail, and complex or disturbing social and political content. Discrimination, "
        "extremism, trauma, grief, and human suffering may be examined honestly and critically. "
        "Sexual content should remain non-explicit."
    ),
    3: (
        "18 — suitable for adults only, following BBFC guidelines. "
        "No content restrictions beyond credibility and serious intent. "
        "Prioritise uncomfortable, confronting, or taboo angles of the topic. "
        "Prioritise honesty and depth over palatability."
    ),
}

CONTENT_FILTER_LANGUAGE = {
    1: "Write for a child. Use simple, clear, warm language. No complex vocabulary, no darkness, no ambiguity.",
    2: "Write for an adult who can handle complexity and provocation. The tone should feel like an honest, intelligent friend who doesn't soften things unnecessarily.",
    3: "Write provocatively without any linguistic restraint. Do not sanitise. Do not hedge. The tone should feel like someone who has no filter and no fear.",
}

CONTENT_FILTER_LABELS = {1: "U/PG", 2: "15", 3: "18"}

def get_content_filter_language(cf):  return CONTENT_FILTER_LANGUAGE.get(cf, CONTENT_FILTER_LANGUAGE[1])
def get_content_filter_guidance(cf):  return CONTENT_FILTER_GUIDANCE.get(cf, CONTENT_FILTER_GUIDANCE[1])
def get_content_filter_label(cf):     return CONTENT_FILTER_LABELS.get(cf, "U/PG")

# ── EMOTIONAL STATES ──────────────────────────────────────────────────────────

EMOTIONAL_STATES = {
    "depressed": {
        "description": "low oxygen, low pulse — subdued, withdrawn, low energy",
        "prompt_modifier": "The person reading this is feeling low and withdrawn. Prioritise warmth, wonder, and the unexpected beauty of ordinary things.",
        "topic_modifier": "Favour topics that are grounding, human, and quietly uplifting. Avoid topics that are confrontational, abstract, or heavy.",
    },
    "stressed": {
        "description": "high oxygen, low pulse — tense, overloaded, anxious",
        "prompt_modifier": "The person reading this is feeling stressed and overwhelmed. Prioritise clarity, perspective-shifting, and calm provocation.",
        "topic_modifier": "Favour topics that offer perspective on human systems, history, or problem-solving. Avoid topics that add to a sense of chaos or overwhelm.",
    },
    "excited": {
        "description": "high oxygen, high pulse — energised, open, ready",
        "prompt_modifier": "The person reading this is feeling excited and energised. Prioritise the unexpected, the radical, and the generative.",
        "topic_modifier": "Favour topics that are at the edges of human knowledge or practice. Embrace the strange and the ambitious.",
    },
    "focused": {
        "description": "low oxygen, high pulse — concentrated, purposeful, ready to work",
        "prompt_modifier": "The person reading this is in a focused, purposeful state. Prioritise precision, specificity, and actionability.",
        "topic_modifier": "Favour topics that have clear structure, defined problems, or practical dimensions. Avoid topics that are too diffuse or open-ended.",
    },
}

EMOTIONAL_OUTPUT_STRUCTURE = {
    "depressed": (
        "Find the single most human detail in the sources — a name, a place, an ordinary object "
        "that makes the fact feel real and close. The sentence should land gently, not hammer."
    ),
    "stressed": (
        "Find the fact that reframes everything — something that puts the topic in perspective "
        "and makes the reader feel the world is more intelligible than they thought. "
        "The sentence should feel like a release valve."
    ),
    "excited": (
        "Find the most surprising, generative, or door-opening fact. "
        "Pick the finding that makes you want to know more, not the one that closes things down."
    ),
    "focused": (
        "Find the most concrete, specific, actionable fact available. "
        "It must contain at least one of: a number, a proper noun, a date, or a named place. "
        "No abstraction. The sentence should feel like a tool."
    ),
}

def get_soft_power_source_framing(soft_power_dial: float) -> str:
    if soft_power_dial < 0.35:
        return (
            "This is a country with strong global cultural influence. "
            "Prioritise sources that reflect its global reach, internal debates, "
            "or the gap between its self-image and outside perception."
        )
    elif soft_power_dial < 0.65:
        return (
            "This is a mid-tier country with regional but not global influence. "
            "Prioritise sources that reflect its specific regional position "
            "and avoid defaulting to Western coverage of it."
        )
    else:
        return (
            "This is a country with low global visibility. "
            "Actively prioritise sources from within the country itself — "
            "local media, community organisations, regional outlets, oral histories. "
            "Avoid Western wire services or foreign NGOs writing about it from outside."
        )

def get_emotional_state_data(state):
    return EMOTIONAL_STATES.get(state, EMOTIONAL_STATES["focused"])

# ── OVERTON WINDOW ─────────────────────────────────────────────────────────────

OVERTON_SOURCE_TIERS = [
    (0.0, {"government", "major_newspaper", "public_broadcaster", "reference", "international_org"}),
    (0.2, {"university", "research_institute", "industry_body", "think_tank", "library", "archive", "museum", "cultural_institution"}),
    (0.4, {"ngo", "magazine", "news_site"}),
    (0.6, {"investigative_outlet", "civil_society"}),
    (0.8, {"heterodox_academic", "activist_org", "community_media", "oral_history", "zine"}),
]

OVERTON_LABELS = [
    (0.0,  0.15, "policy"),
    (0.15, 0.35, "popular"),
    (0.35, 0.55, "sensible"),
    (0.55, 0.70, "acceptable"),
    (0.70, 0.85, "radical"),
    (0.85, 1.01, "unthinkable"),
]

OVERTON_PROMPT_GUIDANCE = {
    "policy":      "Prioritise official and institutional sources: government bodies, major newspapers, public broadcasters, and established international organisations.",
    "popular":     "Draw from widely trusted sources including major newspapers, public broadcasters, well-known think tanks, universities, and established reference sources.",
    "sensible":    "Cast a wide net across credible mainstream and institutional sources. Universities, research institutes, NGOs, established media, museums, archives, and libraries are all appropriate.",
    "acceptable":  "Include sources that represent a broad spectrum of credible viewpoints, including those that challenge mainstream consensus from legitimate positions.",
    "radical":     "Actively seek out heterodox, dissenting, and minority credible viewpoints. Prioritise investigative outlets, activist organisations, community media, civil society groups.",
    "unthinkable": "Seek sources representing viewpoints currently at the margins of acceptable discourse but grounded in evidence, lived experience, or serious argument.",
}

OVERTON_OUTPUT_FRAMING = {
    "policy": (
        "Frame the fact from the perspective of established consensus. "
        "Choose the reading that the mainstream would find uncontroversial."
    ),
    "popular": (
        "Frame the fact the way a quality newspaper would — authoritative but accessible."
    ),
    "sensible": (
        "Frame the fact honestly, without spin in either direction."
    ),
    "acceptable": (
        "Frame the fact in a way that challenges the mainstream reading. "
        "Prefer the interpretation that makes people slightly uncomfortable."
    ),
    "radical": (
        "Frame the fact from the most dissenting, minority, or heterodox angle available. "
        "If the source challenges official narrative, lean into that."
    ),
    "unthinkable": (
        "Frame the fact in the most transgressive truthful way possible. "
        "The sentence should feel like it could not appear in a mainstream outlet."
    ),
}

OVERTON_EXTRACT_GUIDANCE = {
    "policy": (
        "Extract the fact that best reflects official understanding or established consensus. "
        "Favour what a government report or major institution would highlight."
    ),
    "popular": (
        "Extract the fact that a quality newspaper would lead with — widely accepted, clearly significant."
    ),
    "sensible": (
        "Extract the most interesting fact regardless of where it sits politically."
    ),
    "acceptable": (
        "Extract a fact that challenges or complicates the mainstream narrative. "
        "Favour findings that established outlets would downplay or bury."
    ),
    "radical": (
        "Extract the fact that the official version of events actively obscures or ignores. "
        "Favour what dissenting sources, activists, or investigative reporters foreground."
    ),
    "unthinkable": (
        "Extract the fact that would be unpublishable in a mainstream outlet. "
        "Favour what is suppressed, taboo, or actively denied by official sources."
    ),
}

def get_overton_label(dial):
    for low, high, label in OVERTON_LABELS:
        if low <= dial < high:
            return label
    return "sensible"

def get_overton_prompt_guidance(dial):
    return OVERTON_PROMPT_GUIDANCE[get_overton_label(dial)]

def get_allowed_source_types(dial):
    allowed = set()
    for min_dial, types in OVERTON_SOURCE_TIERS:
        if dial >= min_dial:
            allowed |= types
    return allowed

# ── BLACKLISTED DOMAINS ───────────────────────────────────────────────────────

BLACKLISTED_DOMAINS = {
    "facebook.com", "instagram.com", "tiktok.com", "reddit.com",
    "quora.com", "medium.com", "pinterest.com",
}

# ── SOFT POWER / COUNTRY SELECTION ────────────────────────────────────────────

def load_soft_power_csv(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                score = float(row["score"].strip())
            except (ValueError, TypeError):
                continue
            out[row["country"].strip()] = score
    if not out:
        raise ValueError("No valid countries loaded.")
    return out

def _minmax(values):
    mn, mx = min(values), max(values)
    if mx == mn:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) for v in values]

def choose_country(soft_power, soft_power_dial, temperature):
    countries = list(soft_power.keys())
    scores = [soft_power[c] for c in countries]
    t = _minmax(scores)
    preference = [(1.0 - soft_power_dial) * ti + soft_power_dial * (1.0 - ti) for ti in t]
    mean_pref = sum(preference) / len(preference)
    logits = [(p - mean_pref) / temperature for p in preference]
    m = max(logits)
    exps = [math.exp(l - m) for l in logits]
    total = sum(exps)
    probs = [e / total for e in exps]
    return random.choices(countries, weights=probs, k=1)[0]

# ── UTILITIES ─────────────────────────────────────────────────────────────────

def domain_of(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def clean_json_text(text):
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[1].rsplit("```", 1)[0].strip()
    cleaned = _repair_json(cleaned)
    return cleaned

def _repair_json(text):
    text = text.strip()
    if not text:
        return text
    open_braces   = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    text = re.sub(r",\s*$", "", text)
    text += "}" * max(0, open_braces)
    text += "]" * max(0, open_brackets)
    return text

def extract_message_content(response):
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    outputs = getattr(response, "outputs", None)
    if outputs:
        for entry in outputs:
            if getattr(entry, "type", None) == "message.output":
                content = getattr(entry, "content", None)
                if isinstance(content, str):
                    return content.strip()
    raise ValueError(f"Could not extract assistant message content from response:\n{response}")

def url_looks_fetchable(url):
    blocked_markers = ["captcha", "verify you are not a bot", "security verification",
                       "access denied", "forbidden", "cloudflare", "please enable javascript"]
    try:
        response = requests.get(
            url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            allow_redirects=True,
        )
    except Exception as e:
        return False, f"request failed: {e}"
    if response.status_code >= 400:
        return False, f"http {response.status_code}"
    text = response.text[:5000].lower()
    for marker in blocked_markers:
        if marker in text:
            return False, f"blocked page detected: {marker}"
    return True, "ok"

def download_image(url, filename=None):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        images_dir = os.path.join(script_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        session = requests.Session()
        session.headers.update({
            "User-Agent": "UnblockerProject/1.0 (educational research tool) Python/requests",
        })
        time.sleep(2)
        response = session.get(url, timeout=30, allow_redirects=True)
        response.raise_for_status()
        ct  = response.headers.get("content-type", "")
        ext = ".jpg" if "jpeg" in ct or "jpg" in ct else ".png" if "png" in ct else ".webp" if "webp" in ct else os.path.splitext(url.split("?")[0])[-1] or ".jpg"
        if not filename:
            filename = f"delta_image_{int(time.time())}{ext}"
        output_path = os.path.join(images_dir, filename)
        with open(output_path, "wb") as f:
            f.write(response.content)
        print(f"  Image saved: {output_path}")
        return output_path
    except Exception as e:
        print(f"  Image download failed: {e}")
        return None

def process_image_for_thermal(input_path):
    if not HAS_PIL:
        print("  PIL not available — skipping thermal processing")
        return None
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        thermal_dir = os.path.join(script_dir, "thermal")
        os.makedirs(thermal_dir, exist_ok=True)
        img = PILImage.open(input_path).convert("L")
        thermal_width = 384
        thermal_height = int(thermal_width * img.height / img.width)
        img = img.resize((thermal_width, thermal_height), PILImage.LANCZOS)
        from PIL import ImageEnhance
        img = ImageEnhance.Contrast(img).enhance(1.8)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        img = img.convert("1", dither=PILImage.Dither.FLOYDSTEINBERG)
        filename = os.path.splitext(os.path.basename(input_path))[0] + "_thermal.png"
        output_path = os.path.join(thermal_dir, filename)
        img.save(output_path)
        print(f"  Thermal image saved: {output_path}")
        return output_path
    except Exception as e:
        print(f"  Thermal processing failed: {e}")
        return None

# ── SOURCE FILTERING ──────────────────────────────────────────────────────────

def is_good_source(source, allowed_source_types, strict=True):
    url         = source.get("url", "").strip()
    source_type = source.get("source_type", "").strip().lower()
    try:    credibility       = float(source.get("credibility_score", 0))
    except: credibility       = 0.0
    try:    country_relevance = float(source.get("country_relevance_score", 0))
    except: country_relevance = 0.0
    try:    topic_relevance   = float(source.get("topic_relevance_score", 0))
    except: topic_relevance   = country_relevance

    if not url.startswith("http"):                  return False
    domain = domain_of(url)
    if not domain or domain in BLACKLISTED_DOMAINS: return False
    if source_type not in allowed_source_types:     return False

    if strict:
        if credibility < 0.60:        return False
        if country_relevance < 0.40:  return False
        if topic_relevance < 0.55:    return False
    else:
        if credibility < 0.40:        return False
        if country_relevance < 0.25:  return False
        if topic_relevance < 0.35:    return False

    return True

def source_score(source):
    try:    c  = float(source.get("credibility_score", 0) or 0)
    except: c  = 0.0
    try:    cr = float(source.get("country_relevance_score", 0) or 0)
    except: cr = 0.0
    try:    tr = float(source.get("topic_relevance_score", 0) or 0)
    except: tr = cr
    return (0.45 * c) + (0.20 * cr) + (0.35 * tr)

def dedupe_and_rank_sources(sources):
    ranked = sorted(sources, key=source_score, reverse=True)
    seen_urls, domain_counts, final = set(), {}, []
    for s in ranked:
        url = s.get("url", "").strip()
        if not url or url in seen_urls: continue
        domain = domain_of(url)
        if domain_counts.get(domain, 0) >= 2: continue
        seen_urls.add(url)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        final.append(s)
        if len(final) >= MAX_FINAL_SOURCES: break
    return final

# ── MISTRAL: SOURCE FINDING ───────────────────────────────────────────────────

def discover_specific_subject(country, topic, overton_dial, emotional_state, content_filter):
    overton_guidance = get_overton_prompt_guidance(overton_dial)
    state_data       = get_emotional_state_data(emotional_state)
    topic_modifier   = state_data["topic_modifier"]
    filter_guidance  = get_content_filter_guidance(content_filter)

    response = client.chat.complete(
        model="mistral-large-latest",
        messages=[
            {"role": "system", "content": (
                "You are a cultural researcher with deep knowledge of obscure, niche, and non-Western subjects. "
                "You specialise in finding specific named things that are genuinely surprising. "
                "REJECT: treaty signings, ratifications, policy positions, diplomatic agreements. "
                "REJECT: anything that is merely an example of a global trend. "
                "REJECT: anything that could apply to any small country. "
                "ACCEPT: specific people, films, bands, movements, events, artworks unique to this place. "
                "REJECT: current or recent exhibitions, press releases, anniversary shows, retrospectives. "
                "REJECT: anything whose main interest is that it is currently on display somewhere. "
                "ACCEPT: the original movement, person, or event — not its contemporary celebration. "
                "REJECT: current or recent exhibitions, press releases, anniversary shows, retrospectives. "
                "REJECT: anything whose main interest is that it is currently on display somewhere. "
                "ACCEPT: the original movement, person, or event — not its contemporary celebration. "
                f"Overton guidance: {overton_guidance} "
                f"Content guidance: {filter_guidance} "
                f"Emotional context: {topic_modifier}"
            )},
            {"role": "user", "content": "\n".join([
                f"Country: {country}",
                f"Topic: {topic}",
                "",
                f"Find the single most interesting, surprising, or overlooked specific example of this topic in {country}.",
                "It must be a real named thing — a person, film, band, movement, event, book, artwork, or organisation.",
                f"Favour things well-known inside {country} but invisible internationally.",
                "Ask yourself: would this surprise a well-read person? If not, pick something else.",
                "If the topic has no direct presence in this country, find the most interesting indirect connection.",
                "",
                "Return ONLY this JSON:",
                "{",
                '  "name": "specific name",',
                '  "type": "person|film|band|movement|book|artwork|organisation|event",',
                '  "why_interesting": "one sentence",',
                '  "search_query": "best search query to find sources about this"',
                "}",
            ])},
        ],
        temperature=0.9,
        max_tokens=200,
    )

    text = response.choices[0].message.content.strip()
    try:
        data = json.loads(clean_json_text(text))
        print(f"  Discovered: {data.get('name')} ({data.get('type')}) — {data.get('why_interesting')}")
        return data
    except json.JSONDecodeError:
        print(f"  Discovery parse failed, proceeding without subject")
        return None

def find_sources_with_mistral(country, topic, overton_dial, content_filter, emotional_state, soft_power_dial, strict=True):
    overton_guidance     = get_overton_prompt_guidance(overton_dial)
    allowed_source_types = get_allowed_source_types(overton_dial)
    allowed_types_str    = "|".join(sorted(allowed_source_types))
    filter_guidance      = get_content_filter_guidance(content_filter)
    state_data           = get_emotional_state_data(emotional_state)
    topic_modifier       = state_data["topic_modifier"]
    soft_power_framing   = get_soft_power_source_framing(soft_power_dial)

    regional_fallback = (
        f"If you cannot find sources in English, include sources in other languages — "
        f"French, Spanish, Portuguese, Arabic, or the local language of {country} are all acceptable. "
        f"If you cannot find sources from within {country}, include regional or diaspora sources."
    ) if soft_power_dial > 0.4 else ""

    prompt = "\n".join([
        "You are a cultural researcher and source finder working in two steps.",
        "",
        f"Country: {country}",
        f"Topic: {topic}",
        "",
        "STEP 1 — DISCOVERY",
        f"Find the single most interesting, surprising, or overlooked specific example of this topic in {country}.",
        "It must be a real, named thing — a specific person, film, band, movement, event, book, artwork, or organisation.",
        f"Favour things that are well-known inside {country} but invisible internationally.",
        "Favour things that would surprise someone who thinks they know this country.",
        "Do NOT pick something internationally famous.",
        "Do NOT pick something generic or abstract.",
        "Do NOT pick treaty signings, ratifications, diplomatic agreements, or policy positions.",
        "Do NOT pick things that are merely examples of a general global trend.",
        f"Do NOT pick things that could apply to any small country — find something specific to {country}.",
        "Ask yourself: would this surprise a well-read person? If not, pick something else.",
        "Ask yourself: is there a human story, a specific event, a named person, a cultural artefact here?",
        "If the topic has no direct presence in this country, find the most interesting indirect connection.",
        "",
        "STEP 2 — SOURCE SEARCH",
        "Now search the web for real, substantive sources specifically about that named subject.",
        "If you cannot find at least one good source about it, pick a different subject and try again.",
        "Only finalise a subject you can actually find sources for.",
        regional_fallback,
        "",
        f"Source selection guidance: {overton_guidance}",
        f"Content guidance: {filter_guidance}",
        f"Emotional context: {topic_modifier}",
        f"Source origin guidance: {soft_power_framing}",
        "",
        "Rules:",
        "- Do NOT return concert listings, event calendars, ticket pages, or mainstream entertainment news.",
        "- Do NOT return sources about the general topic — only about the specific named subject.",
        f"- Only include sources matching these types: {allowed_types_str}",
        "- Return between 1 and 3 sources maximum.",
        "- Return ONLY JSON, nothing else.",
        "",
        "JSON schema:",
        "{",
        '  "subject": {',
        '    "name": "specific name of the subject",',
        '    "type": "person|film|band|movement|book|artwork|organisation|event",',
        '    "why_interesting": "one sentence explaining why this is surprising or significant"',
        '  },',
        '  "sources": [',
        '    {',
        '      "title": "string",',
        '      "url": "string",',
        '      "publisher": "string",',
        '      "country": "string",',
        f'      "source_type": "{allowed_types_str}",',
        '      "credibility_score": 0.0,',
        '      "country_relevance_score": 0.0,',
        '      "topic_relevance_score": 0.0,',
        '      "why_credible": "string",',
        '      "why_relevant": "string"',
        '    }',
        '  ]',
        '}',
    ])

    for attempt in range(3):
        try:
            response = client.beta.conversations.start(
                inputs=prompt,
                model="mistral-medium-2505",
                instructions=(
                    "You are a cultural researcher and source finder. "
                    "Your job is to discover a specific interesting named subject in the given country "
                    "related to the given topic, then find real web sources about it. "
                    "REJECT: treaty signings, ratifications, policy positions, diplomatic agreements. "
                    "REJECT: anything that is merely an example of a global trend. "
                    "REJECT: anything that could apply to any small country. "
                    "ACCEPT: specific people, events, stories, cultural artefacts, movements unique to this place. "
                    "You must verify sources exist before returning them — do not invent URLs. "
                    "If you cannot find sources about your first choice of subject, try a different subject. "
                    f"Overton guidance: {overton_guidance} "
                    f"Content guidance: {filter_guidance} "
                    f"Emotional context: {topic_modifier} "
                    f"Source origin guidance: {soft_power_framing} "
                    f"{regional_fallback} "
                    "Do NOT return concert listings, event calendars, or mainstream entertainment news. "
                    "Return ONLY valid JSON matching the schema exactly. "
                    "NEVER return explanatory text. ALWAYS return valid JSON."
                ),
                tools=[{"type": "web_search"}],
                completion_args={"temperature": 0.4, "top_p": 0.9},
                timeout_ms=120000,
            )
            break
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  Retry {attempt+1} after error: {e}")
            time.sleep(2)

    text    = extract_message_content(response)
    cleaned = clean_json_text(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"  Failed to parse JSON: {e}")
        return [], None

    subject    = data.get("subject")
    candidates = data.get("sources", [])

    if subject:
        print(f"  Discovered: {subject.get('name')} ({subject.get('type')}) — {subject.get('why_interesting')}")

    filtered = [s for s in candidates if is_good_source(s, allowed_source_types, strict=strict)]
    return dedupe_and_rank_sources(filtered), subject

# ── MISTRAL: TOPIC GENERATION ─────────────────────────────────────────────────

def generate_delta_topic(original_topic: str, delta: float) -> str:
    if delta < 0.05:
        instruction = (
            f"Generate the single most iconic, universally recognised visual symbol of '{original_topic}'. "
            "The most obvious possible image — what every textbook would use."
        )
    elif delta < 0.10:
        instruction = (
            f"Generate a visual subject that directly and literally illustrates '{original_topic}'. "
            "It should be an immediately obvious choice."
        )
    elif delta < 0.15:
        instruction = (
            f"Generate a visual subject that clearly depicts '{original_topic}'. "
            "A specific well-known example of the topic itself."
        )
    elif delta < 0.20:
        instruction = (
            f"Generate a visual subject closely associated with '{original_topic}' — "
            "something that appears in documentaries or encyclopedias about it."
        )
    elif delta < 0.25:
        instruction = (
            f"Generate a visual subject that is a recognisable part or component of '{original_topic}' "
            "rather than the whole thing."
        )
    elif delta < 0.30:
        instruction = (
            f"Generate a specific object, place, or person strongly associated with '{original_topic}' "
            "but not a direct depiction of it."
        )
    elif delta < 0.35:
        instruction = (
            f"Generate a visual subject from the same world as '{original_topic}' — "
            "something you'd find nearby but wouldn't use as the main illustration."
        )
    elif delta < 0.40:
        instruction = (
            f"Generate a visual subject that shares a clear thematic connection to '{original_topic}' "
            "but could belong to a different context entirely."
        )
    elif delta < 0.45:
        instruction = (
            f"Generate a visual subject whose mood or atmosphere echoes '{original_topic}' "
            "without depicting it directly."
        )
    elif delta < 0.50:
        instruction = (
            f"Generate a visual subject that shares one quality with '{original_topic}' — "
            "scale, texture, colour, or feeling — but is otherwise unrelated."
        )
    elif delta < 0.55:
        instruction = (
            f"Generate a visual subject that would not obviously illustrate '{original_topic}' "
            "but where a thoughtful person could draw a connection."
        )
    elif delta < 0.60:
        instruction = (
            f"Generate a visual subject that rhymes conceptually with '{original_topic}' — "
            "a parallel structure in a completely different domain."
        )
    elif delta < 0.65:
        instruction = (
            f"Generate a visual subject with a poetic or metaphorical relationship to '{original_topic}'. "
            "The connection should require a moment of thought to see."
        )
    elif delta < 0.70:
        instruction = (
            f"Generate a visual subject that could serve as an unexpected symbol for '{original_topic}' "
            "in a way most people would not anticipate."
        )
    elif delta < 0.75:
        instruction = (
            f"Generate a visual subject where the connection to '{original_topic}' is oblique — "
            "present but deniable."
        )
    elif delta < 0.80:
        instruction = (
            f"Generate a visual subject that feels atmospherically adjacent to '{original_topic}' "
            "but where most people would not see the link."
        )
    elif delta < 0.85:
        instruction = (
            f"Generate a visual subject from a completely different domain to '{original_topic}' "
            "with only the faintest residual connection."
        )
    elif delta < 0.90:
        instruction = (
            f"Generate a visual subject that appears unrelated to '{original_topic}' — "
            "the connection, if any, should be private or accidental."
        )
    elif delta < 0.95:
        instruction = (
            f"Generate a visual subject with no meaningful relationship to '{original_topic}'. "
            "Something visually striking chosen almost at random."
        )
    else:
        instruction = (
            f"Generate a visual subject with absolutely no relationship to '{original_topic}'. "
            "Ignore the topic entirely and pick something purely for visual interest."
        )

    response = client.chat.complete(
        model="mistral-small-latest",
        messages=[{"role": "user", "content": (
            f"{instruction} "
            "It must exist as a real photograph on Wikimedia Commons. "
            "Pick something real, well-known, and visually rich — a famous landmark, "
            "a well-documented natural phenomenon, an iconic piece of architecture, "
            "a known historical event or person. "
            "Avoid abstract or empty scenes. Avoid anything described as 'empty' or 'abandoned'. "
            "Avoid the single most famous or iconic example — pick the second or third most interesting option. "
            "Avoid these clichés: Rodin's Thinker, Mona Lisa, Eiffel Tower, Great Wall of China, "
            "Stonehenge, Taj Mahal, Colosseum, Statue of Liberty, Big Ben, Sydney Opera House. "
            "Return only the subject name. No explanation. No punctuation at the end. 2-5 words maximum."
        )}],
        temperature=0.3 + (delta * 0.7),
        max_tokens=15,
    )
    return response.choices[0].message.content.strip()

# ── OPEN NOTEBOOK ─────────────────────────────────────────────────────────────

def get_latest_notebook():
    response = requests.get(f"{OPEN_NOTEBOOK_API}/api/notebooks?order_by=updated+desc", timeout=30)
    response.raise_for_status()
    notebooks = response.json()
    if not notebooks:
        raise RuntimeError("No notebooks found.")
    return notebooks[0]

def get_sources_in_notebook(notebook_id):
    response = requests.get(f"{OPEN_NOTEBOOK_API}/api/sources", params={"notebook_id": notebook_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []

def delete_source(source_id):
    response = requests.delete(f"{OPEN_NOTEBOOK_API}/api/sources/{source_id}", timeout=60)
    ok = 200 <= response.status_code < 300
    return ok, response.text

def clear_notebook_sources(notebook_id):
    initial = get_sources_in_notebook(notebook_id)
    print(f"  Found {len(initial)} existing source(s). Deleting...")
    to_delete = [s.get("id") for s in initial if s.get("id")]

    for attempt in range(3):
        if not to_delete:
            break

        deleted, failed = [], []

        def do_delete(sid):
            ok, body = delete_source(sid)
            (deleted if ok else failed).append(sid)

        threads = [threading.Thread(target=do_delete, args=(sid,)) for sid in to_delete]
        for t in threads: t.start()
        for t in threads: t.join(timeout=60)

        time.sleep(2)
        remaining = get_sources_in_notebook(notebook_id)
        remaining_ids = {s.get("id") for s in remaining if s.get("id")}
        to_delete = [sid for sid in to_delete if sid in remaining_ids]

        if not to_delete:
            break
        print(f"  {len(to_delete)} still stuck, retrying (attempt {attempt + 2})...")
        time.sleep(3)

    remaining = get_sources_in_notebook(notebook_id)
    print(f"  Remaining after clear: {len(remaining)}")
    return {"remaining": len(remaining)}

def add_link_source(notebook_id, url, retries=5, delay=2.0):
    data = {"type": "link", "notebook_id": notebook_id, "url": url, "embed": "true", "async_processing": "true"}
    for attempt in range(retries):
        try:
            r = requests.post(f"{OPEN_NOTEBOOK_API}/api/sources", data=data, timeout=120)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError:
            if r.status_code == 500 and attempt < retries - 1:
                time.sleep(delay)
                delay *= 1.5
            else:
                raise
    raise Exception(f"Failed to add source after {retries} retries: {url}")

def add_text_source(notebook_id, title, text):
    data = {"type": "text", "notebook_id": notebook_id, "title": title, "content": text,
            "embed": "true", "async_processing": "true"}
    for attempt in range(3):
        try:
            r = requests.post(f"{OPEN_NOTEBOOK_API}/api/sources", data=data, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2: raise
            print(f"  add_text_source retry {attempt+1}: {e}")
            time.sleep(2)

def wait_for_source_processing(source_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{OPEN_NOTEBOOK_API}/api/sources/{source_id}", timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("embedded") and data.get("embedded_chunks", 0) > 0:
                return True
            if data.get("status", "").lower() in ("failed", "error"):
                return False
        except Exception as e:
            print(f"  Status check failed: {e}")
        time.sleep(5)
    return False

def fetch_source_content(source_id):
    try:
        r = requests.get(f"{OPEN_NOTEBOOK_API}/api/sources/{source_id}", timeout=30)
        r.raise_for_status()
        return r.json().get("full_text") or None
    except Exception as e:
        print(f"  Could not fetch source content: {e}")
        return None

def get_wikipedia_summary(url):
    try:
        title    = url.rstrip("/").split("/wiki/")[-1]
        response = requests.get(f"{WIKIPEDIA_API}/{title}", timeout=20, headers={"User-Agent": "open-notebook-pipeline/1.0"})
        if response.status_code != 200:
            return None
        return response.json().get("extract", "").strip() or None
    except Exception as e:
        print(f"  Wikipedia API error: {e}")
        return None

# ── PROMPT GENERATION ─────────────────────────────────────────────────────────

def get_notebook_prompt(emotional_state, overton_dial, subject=None):
    extract_guidance    = OVERTON_EXTRACT_GUIDANCE[get_overton_label(overton_dial)]
    emotional_structure = EMOTIONAL_OUTPUT_STRUCTURE[emotional_state]

    subject_focus = (
        f"The sources are specifically about: {subject['name']} ({subject['type']}) — "
        f"{subject['why_interesting']} "
        f"Focus your extraction on this specific subject, not the topic in general. "
    ) if subject else ""

    return (
        f"{subject_focus}"
        f"{extract_guidance} "
        f"{emotional_structure} "
        "The result must be cohesive and faithful to the sources. "
        "Write it as one punchy sentence in the style of an intelligent newspaper headline. "
        "Do NOT write poetry, metaphors, or travel writing. "
        "Do NOT use 'while', 'whereas', or 'meanwhile' to join unrelated facts. "
        "Do NOT end with a summary conclusion or evaluative statement like 'sparking', 'marking', "
        "'highlighting', 'demonstrating', 'showcasing', 'challenging', or 'promoting'. "
        "End on a concrete fact, not a judgement. "
        "No preamble. No caveats. No attribution. No citations. One sentence only."
    )
# ── QUERY / OUTPUT ────────────────────────────────────────────────────────────

def _run_query(prompt: str, source_text: str, content_filter: int, overton_dial: float) -> str:
    overton_framing = OVERTON_OUTPUT_FRAMING[get_overton_label(overton_dial)]
    response = client.chat.complete(
        model="mistral-large-latest",
        messages=[
            {"role": "system", "content": (
                f"{CONTENT_FILTER_SYSTEM[content_filter]}\n\n"
                "Answer using ONLY the source texts provided. "
                "Extract ONE specific fact, statistic, name, date, or concrete piece of information. "
                "NEVER invent or extrapolate dates, statistics, or events not explicitly stated in the source text. "
                "If you are not certain a detail appears verbatim in the sources, omit it entirely. "
                "Do NOT distinguish between publication dates and event dates — if unsure, omit the date. "
                "One sentence only. No preamble. No caveats. No attribution."
            )},
            {"role": "user", "content": f"SOURCE TEXTS:\n\n{source_text}\n\n---\n\nQUESTION: {prompt}"},
        ],
        temperature=0.4,
        max_tokens=150,
    )
    return response.choices[0].message.content.strip()

def query_with_mistral_directly(prompt, source_ids, uploaded, content_filter, overton_dial):
    web_texts = []
    for u in uploaded:
        source_id = u.get("source_id")
        if not source_id:
            continue
        text = fetch_source_content(source_id)
        if text:
            web_texts.append(f"[WEB SOURCE: {u.get('url', '')}]\n{text[:3000]}")
    if not web_texts:
        return "No source content could be retrieved."
    return _run_query(prompt, "\n\n---\n\n".join(web_texts), content_filter, overton_dial)

# ── WIKIMEDIA IMAGE ───────────────────────────────────────────────────────────

def find_wikimedia_image(topic, country=""):
    queries = [topic] if not country else [f"{topic} {country}", topic]
    for query in queries:
        try:
            time.sleep(3)
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action":       "query",
                    "generator":    "search",
                    "gsrnamespace": 6,
                    "gsrsearch":    query,
                    "gsrlimit":     20,
                    "prop":         "imageinfo",
                    "iiprop":       "url|mime|extmetadata",
                    "iiurlwidth":   400,
                    "format":       "json",
                },
                timeout=20,
                headers={"User-Agent": "UnblockerProject/1.0 (educational research tool) Python/requests"},
            )
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})

            # build a candidate list first, pick best, attempt once
            candidates = []
            for page in pages.values():
                ii    = page.get("imageinfo", [{}])[0]
                url   = ii.get("thumburl") or ii.get("url")
                mime  = ii.get("mime", "")
                title = page.get("title", "").replace("File:", "")
                if not url: continue
                if not mime.startswith("image/"): continue
                if "svg" in mime: continue
                if url.lower().endswith((".djvu", ".pdf", ".tiff", ".tif")): continue
                if title in _used_image_titles: continue
                candidates.append((page, ii, url, title))

            # try candidates one at a time with a single attempt each
            for page, ii, url, title in candidates:
                try:
                    test = requests.get(
                        url, timeout=15,
                        headers={"User-Agent": "UnblockerProject/1.0 (educational research tool) Python/requests"},
                    )
                    if test.status_code == 200:
                        _used_image_titles.append(title)
                        desc = ii.get("extmetadata", {}).get("ImageDescription", {}).get("value", "")
                        return {
                            "image_url":       url,
                            "image_title":     title,
                            "image_source":    f"https://commons.wikimedia.org/wiki/{page.get('title','').replace(' ','_')}",
                            "why_interesting": desc or f"Wikimedia Commons: {topic}",
                        }
                    elif test.status_code == 429:
                        print(f"  Rate limited on {title} — skipping query")
                        return {"rate_limited": True}
                        break
                    else:
                        continue
                except Exception:
                    continue

        except Exception as e:
            print(f"  Wikimedia search failed for '{query}': {e}")
    return None

def find_unsplash_image(topic):
    print(f"  [Unsplash] searching for: {topic}")
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={
                "query":       topic,
                "per_page":    10,
                "orientation": "landscape",
            },
            headers={
                "Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
            },
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        for photo in results:
            title = photo.get("id", "")
            if title in _used_image_titles:
                continue
            url = photo["urls"]["regular"]
            _used_image_titles.append(title)
            description = (
                photo.get("description") or
                photo.get("alt_description") or
                f"Unsplash: {topic}"
            )
            attribution = photo["user"]["name"]
            print(f"  [Unsplash] found image for: {topic}")
            return {
                "image_url":       url,
                "image_title":     description,
                "image_source":    photo["links"]["html"],
                "why_interesting": f"Photo by {attribution} on Unsplash",
            }
    except Exception as e:
        print(f"  Unsplash search failed for '{topic}': {e}")
    return None

# ── PRINTER (direct TTL, no Arduino) ─────────────────────────────────────────

def send_print(text, image_path=None, qr_url=None, printer_port=None, no_print=False):
    if no_print:
        print("\n── PRINT OUTPUT (no-print mode) ──────────────────────────")
        print(text)
        print("──────────────────────────────────────────────────────────\n")
        return

    port = printer_port or PRINTER_PORT
    try:
        from escpos.printer import Serial as EscposSerial
        from PIL import ImageDraw, ImageFont

        PRINTER_WIDTH = 384
        FONT_SIZE     = 24
        GAP           = 20

        try:
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", FONT_SIZE)
            font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", FONT_SIZE)
        except Exception:
            font_large = ImageFont.load_default()
            font_body  = ImageFont.load_default()

        def text_to_lines(txt, font, max_width):
            words   = txt.split()
            lines   = []
            current = ""
            for word in words:
                test = (current + " " + word).strip()
                bbox = font.getbbox(test)
                if bbox[2] <= max_width:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
            return lines

        def line_height(font):
            bbox = font.getbbox("Ag")
            return bbox[3] - bbox[1] + 6

        title_text_lines  = ["Unblocking Text"]
        body_lines        = text_to_lines(text, font_body, PRINTER_WIDTH - 8)
        title_image_lines = ["Unblocking Image"]

        lh_large = line_height(font_large)
        lh_body  = line_height(font_body)

        # ── QR code
        url_to_encode = qr_url or "http://192.168.0.239:8765/summaries/"
        qr_obj = qrcode.QRCode(version=2, box_size=3, border=2)
        qr_obj.add_data(url_to_encode)
        qr_obj.make(fit=True)
        qr_pil  = qr_obj.make_image(fill_color="black", back_color="white").convert("L")
        qr_w, qr_h = qr_pil.size
        qr_x    = (PRINTER_WIDTH - qr_w) // 2

        # ── content image — resize to printer width preserving aspect ratio
        content_img = None
        content_h   = 0
        if image_path and os.path.exists(image_path):
            content_img = PILImage.open(image_path).convert("L")
            aspect      = content_img.height / content_img.width
            content_img = content_img.resize(
                (PRINTER_WIDTH, int(PRINTER_WIDTH * aspect)),
                PILImage.LANCZOS
            )
            content_h = content_img.height

        total_height = (
            lh_large * len(title_text_lines)  + GAP +
            lh_body  * len(body_lines)         + GAP * 2 +
            lh_large * len(title_image_lines)  + GAP +
            content_h                          + GAP * 2 +
            qr_h                               + GAP
        )

        # ── draw everything onto one canvas
        canvas = PILImage.new("L", (PRINTER_WIDTH, total_height), 255)
        draw   = ImageDraw.Draw(canvas)
        y      = 0

        # "Unblocking Text" title
        for line in title_text_lines:
            draw.text((4, y), line, font=font_large, fill=0)
            y += lh_large
        y += GAP

        # body text
        for line in body_lines:
            draw.text((4, y), line, font=font_body, fill=0)
            y += lh_body
        y += GAP * 2

        # "Unblocking Image" title
        for line in title_image_lines:
            draw.text((4, y), line, font=font_large, fill=0)
            y += lh_large
        y += GAP

        # content image
        if content_img:
            canvas.paste(content_img, (0, y))
            y += content_h
        y += GAP * 2

        # QR — centred
        canvas.paste(qr_pil, (qr_x, y))

# ── rotate entire canvas 180°
        canvas = canvas.rotate(180)

        # ── split into two canvases
        # QR is at the bottom of the rotated canvas (was last drawn, now at top after rotation)
        # We know qr_h so we can slice it off

        qr_canvas      = canvas.crop((0, 0, PRINTER_WIDTH, qr_h + GAP))
        content_canvas = canvas.crop((0, qr_h + GAP, PRINTER_WIDTH, canvas.height))

        # ── apply vertical correction to content only
        VERTICAL_CORRECTION = 0.85
        corrected_h     = int(content_canvas.height * VERTICAL_CORRECTION)
        content_canvas  = content_canvas.resize((PRINTER_WIDTH, corrected_h), PILImage.LANCZOS)

        # ── convert both to 1-bit
        qr_canvas      = qr_canvas.convert("1", dither=PILImage.Dither.FLOYDSTEINBERG)
        content_canvas = content_canvas.convert("1", dither=PILImage.Dither.FLOYDSTEINBERG)

        qr_path      = "/tmp/print_final_qr.png"
        content_path = "/tmp/print_final_content.png"
        qr_canvas.save(qr_path)
        content_canvas.save(content_path)

        # ── send to printer as two blocks
        printer = EscposSerial(
            devfile=port,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=2,
            dsrdtr=False,
        )
        printer._raw(b"\x1b\x40")
        printer.image(qr_path, impl="bitImageRaster", center=False)
        printer.image(content_path, impl="bitImageRaster", center=False)
        printer.text("\n\n\n\n\n")
        printer._raw(b"\x1d\x56\x00")
        print("  Printed successfully.")

    except Exception as e:
        print(f"  Print failed: {e}")
        print("  Output text was:")
        print(text)

# ── HTML / QR HELPERS ─────────────────────────────────────────────────────────

def generate_source_summary(notebook_answer, uploaded, image_result, delta_topic):
    source_list = "\n".join([
        f"- {u.get('publisher', 'Unknown')}: {u.get('url', '')} — {u.get('why_relevant', '')}"
        for u in uploaded
    ])
    image_source = image_result.get("image_source", "")
    image_title  = image_result.get("image_title", "")
    response = client.chat.complete(
        model="mistral-medium-2505",
        messages=[
            {"role": "system", "content": "You write brief, honest source summaries. For each source, write one sentence. Be concrete and specific. No waffle."},
            {"role": "user", "content": f"TEXT OUTPUT:\n{notebook_answer}\n\nTEXT SOURCES:\n{source_list}\n\nIMAGE: '{image_title}' from {image_source} — related to topic '{delta_topic}'\n\nWrite a brief summary of each source."},
        ],
        temperature=0.3,
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()

def save_html(topic, country, notebook_answer, uploaded, image_result, delta_topic, source_summary,
              overton_label, filter_label, emotional_state, soft_power_dial, semantic_delta):
    timestamp  = time.strftime("%Y%m%d_%H%M%S")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    html_dir   = os.path.join(script_dir, "summaries")
    os.makedirs(html_dir, exist_ok=True)
    html_filename = f"summary_{timestamp}.html"
    html_path     = os.path.join(html_dir, html_filename)
    file_url      = f"http://192.168.0.239:8765/summaries/{html_filename}"

    image_url    = image_result.get("image_url", "")
    image_title  = image_result.get("image_title", "")
    image_source = image_result.get("image_source", "")
    source_rows  = "".join([
        f'<div class="source"><div class="source-publisher">{u.get("publisher","Unknown")}</div>'
        f'<a href="{u.get("url","")}" target="_blank">{u.get("url","")}</a>'
        f'<p>{u.get("why_relevant","")}</p></div>'
        for u in uploaded
    ])

    dial_info = (
        f"Overton: {overton_label} | Filter: {filter_label} | "
        f"State: {emotional_state} | Soft Power: {soft_power_dial:.2f} | "
        f"Semantic Delta: {semantic_delta:.2f}"
    )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{topic} — {country}</title>
<style>body{{font-family:'Georgia',serif;max-width:800px;margin:40px auto;padding:20px;background:#f9f7f2;color:#222;line-height:1.7}}
h1{{font-size:1.4em;font-weight:normal;color:#555}}
.meta{{font-size:.85em;color:#888;margin-bottom:10px}}
.dials{{font-size:.8em;color:#a06030;background:#fff3e0;padding:8px 12px;border-left:3px solid #c8a96e;margin-bottom:20px}}
.sentence{{font-size:1.2em;line-height:1.8;background:#fff;padding:20px;border-left:4px solid #c8a96e;margin:20px 0}}
.image-block{{margin:30px 0}}.image-block img{{max-width:100%;border:1px solid #ddd}}
.image-caption{{font-size:.85em;color:#666;margin-top:6px}}
.source{{background:#fff;padding:14px;margin:10px 0;border:1px solid #eee}}
.source-publisher{{font-weight:bold;font-size:.9em;color:#555}}
.source a{{color:#7a5c2e;font-size:.85em;word-break:break-all}}
.source p{{margin:6px 0 0;font-size:.9em;color:#444}}
.summary{{background:#fff;padding:20px;border:1px solid #eee;font-size:.95em;white-space:pre-wrap}}
h2{{font-size:1.1em;font-weight:bold;margin-top:40px;border-bottom:1px solid #ddd;padding-bottom:6px}}</style>
</head><body>
<h1>{topic} — {country}</h1>
<div class="meta">Generated {time.strftime('%Y-%m-%d %H:%M:%S')}</div>
<div class="dials">{dial_info}</div>
<h2>Output</h2><div class="sentence">{notebook_answer.replace(chr(10), '<br>')}</div>
<h2>Image — {delta_topic}</h2>
<div class="image-block"><img src="{image_url}" alt="{image_title}">
<div class="image-caption">{image_title} — <a href="{image_source}" target="_blank">{image_source}</a></div></div>
<h2>Sources</h2>{source_rows}
<h2>Source Summary</h2><div class="summary">{source_summary}</div>
</body></html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  HTML saved: {html_path}")
    print(f"  URL: {file_url}")
    return {"html_path": html_path, "file_url": file_url}

# ── MAIN BUILD FUNCTION ───────────────────────────────────────────────────────

def build_notebook_for_topic(
    soft_power_dial=0.5,
    overton_dial=0.3,
    semantic_delta=0.4,
    content_filter=2,
    emotional_state="stressed",
    country_override=None,
    topic_override=None,
    no_print=False,
    no_image=False,
    printer_port=None,
):
    print("\n" + "═" * 60)
    print("DIAL SETTINGS")
    print(f"  Soft Power dial:  {soft_power_dial:.2f}  (0=high-power nations, 1=low-power)")
    print(f"  Overton dial:     {overton_dial:.2f}  → {get_overton_label(overton_dial)}")
    print(f"  Semantic delta:   {semantic_delta:.2f}  (0=literal image, 1=abstract)")
    print(f"  Content filter:   {content_filter}  → {get_content_filter_label(content_filter)}")
    print(f"  Emotional state:  {emotional_state}")
    print("═" * 60 + "\n")

    # ── Topic & Country
    soft_power_data = load_soft_power_csv(INDEX_CSV_PATH)
    topic   = topic_override   or pick_topic(overton_dial, emotional_state, content_filter)
    country = country_override or choose_country(soft_power_data, soft_power_dial, SOFT_POWER_TEMPERATURE)
    print(f"Topic:   {topic}")
    print(f"Country: {country}\n")

    # ── Notebook
    notebook    = get_latest_notebook()
    notebook_id = notebook["id"]
    print(f"Notebook: {notebook.get('title','(untitled)')} ({notebook_id})")
    clear_notebook_sources(notebook_id)

    # ── Source search
    sources = []
    subject = None
    attempt = 0
    strict  = True
    while not sources:
        attempt += 1
        print(f"\nSource search attempt {attempt}: {topic} in {country} (strict={strict})")
        try:
            result  = find_sources_with_mistral(
                country, topic, overton_dial, content_filter,
                emotional_state, soft_power_dial, strict=strict
            )
            sources, subject = result if result else ([], None)
            sources = sources or []
        except Exception as e:
            print(f"  Search failed: {e}")

        if not sources:
            if strict and attempt >= 2:
                print("  Relaxing source filters...")
                strict = False
            else:
                topic   = topic_override   or pick_topic(overton_dial, emotional_state, content_filter)
                country = country_override or choose_country(soft_power_data, soft_power_dial, SOFT_POWER_TEMPERATURE)
                print(f"  Retrying with: {topic} in {country}")

    # ── Upload sources
    uploaded, skipped = [], []
    for source in sources:
        url          = source["url"]
        is_wikipedia = "wikipedia.org/wiki/" in url
        if is_wikipedia:
            wiki_text = get_wikipedia_summary(url)
            if not wiki_text:
                skipped.append(url)
                continue
            result = add_text_source(notebook_id, source.get("title", url), wiki_text)
        else:
            ok, reason = url_looks_fetchable(url)
            if not ok:
                skipped.append(url)
                continue
            result = add_link_source(notebook_id, url)
        source_id = result.get("id")
        uploaded.append({
            "url": url, "source_id": source_id,
            "title": result.get("title"), "publisher": source.get("publisher"),
            "why_credible": source.get("why_credible"),
            "why_relevant": source.get("why_relevant"),
        })

    if not uploaded:
        print("No sources uploaded — retrying entire run.")
        return build_notebook_for_topic(
            soft_power_dial=soft_power_dial, overton_dial=overton_dial,
            semantic_delta=semantic_delta, content_filter=content_filter,
            emotional_state=emotional_state, country_override=country_override,
            topic_override=topic_override, no_print=no_print, no_image=no_image,
            printer_port=printer_port,
        )

    # ── Wait for sources to be fully processed before querying
    print("\nWaiting for sources to process...")
    for u in uploaded:
        if u["source_id"]:
            wait_for_source_processing(u["source_id"])

    # ── Query
    notebook_prompt = get_notebook_prompt(emotional_state, overton_dial, subject)
    print(f"\nPrompt:\n  {notebook_prompt}\n")
    source_ids      = [u["source_id"] for u in uploaded if u["source_id"]]
    notebook_answer = query_with_mistral_directly(notebook_prompt, source_ids, uploaded, content_filter, overton_dial)

    print("\n" + "═" * 60)
    print("OUTPUT:")
    print(notebook_answer)
    print("═" * 60 + "\n")

    # ── Image
    image_result = {}
    image_path   = None
    delta_topic  = None

    if not no_image:
        wikimedia_rate_limited = False
        for attempt in range(5):
            delta_topic = generate_delta_topic(topic, semantic_delta)
            print(f"Semantic delta ({semantic_delta:.2f}): '{topic}' → '{delta_topic}'")

            if not wikimedia_rate_limited:
                image_result = find_wikimedia_image(delta_topic) or {}
                if image_result.get("rate_limited"):
                    print("  Wikimedia rate limited — switching to Unsplash")
                    wikimedia_rate_limited = True
                elif image_result.get("image_url"):
                    image_path = download_image(image_result["image_url"])
                    if image_path:
                        break
                else:
                    print(f"  No image found for '{delta_topic}' — trying new delta...")
                    time.sleep(5)
                    continue

            if wikimedia_rate_limited:
                print(f"  Trying Unsplash for '{delta_topic}'...")
                image_result = find_unsplash_image(delta_topic) or {}
                if image_result.get("image_url"):
                    image_path = download_image(image_result["image_url"])
                    if image_path:
                        break
                else:
                    print(f"  No Unsplash image for '{delta_topic}' — trying new delta...")
                    time.sleep(2)
                    continue

    thermal_path = process_image_for_thermal(image_path) if image_path else None
    image_result["image_local_path"] = image_path
    image_result["thermal_path"]     = thermal_path

    # ── HTML
    source_summary = generate_source_summary(notebook_answer, uploaded, image_result, delta_topic or topic)
    html_result    = save_html(
        topic, country, notebook_answer, uploaded, image_result,
        delta_topic or topic, source_summary,
        get_overton_label(overton_dial), get_content_filter_label(content_filter),
        emotional_state, soft_power_dial, semantic_delta,
    )
    html_path = html_result["html_path"]
    file_url  = html_result["file_url"]

    # ── Print
    send_print(notebook_answer, image_path=thermal_path, qr_url=file_url, printer_port=printer_port, no_print=no_print)

    return {
        "country": country,
        "topic": topic,
        "overton_label": get_overton_label(overton_dial),
        "overton_dial": overton_dial,
        "content_filter_label": get_content_filter_label(content_filter),
        "content_filter": content_filter,
        "emotional_state": emotional_state,
        "soft_power_dial": soft_power_dial,
        "semantic_delta": semantic_delta,
        "notebook_answer": notebook_answer,
        "delta_topic": delta_topic,
        "html_path": html_path,
    }

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Standalone prompt tester — no Arduino required.")
    parser.add_argument("--soft-power",  type=float, default=0.5,       metavar="0.0-1.0")
    parser.add_argument("--overton",     type=float, default=0.3,       metavar="0.0-1.0")
    parser.add_argument("--semantic",    type=float, default=0.4,       metavar="0.0-1.0")
    parser.add_argument("--filter",      type=int,   default=2,         choices=[1, 2, 3])
    parser.add_argument("--state",       type=str,   default="stressed",
                        choices=["depressed", "stressed", "excited", "focused"])
    parser.add_argument("--country",     type=str,   default=None)
    parser.add_argument("--topic",       type=str,   default=None)
    parser.add_argument("--printer-port",type=str,   default=PRINTER_PORT)
    parser.add_argument("--no-print",    action="store_true")
    parser.add_argument("--no-image",    action="store_true")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Show config and exit without running")
    args = parser.parse_args()

    if args.dry_run:
        print("\nDRY RUN — config only")
        print(f"  --soft-power  {args.soft_power}  → country bias toward {'low' if args.soft_power > 0.5 else 'high'}-power nations")
        print(f"  --overton     {args.overton}  → {get_overton_label(args.overton)}")
        print(f"  --semantic    {args.semantic}  → image distance from topic")
        print(f"  --filter      {args.filter}  → {get_content_filter_label(args.filter)}")
        print(f"  --state       {args.state}")
        if args.country: print(f"  --country     {args.country}")
        if args.topic:   print(f"  --topic       {args.topic}")
        print(f"  --printer-port {args.printer_port}")
        print(f"  --no-print    {args.no_print}")
        print(f"  --no-image    {args.no_image}")
        return

    build_notebook_for_topic(
        soft_power_dial=args.soft_power,
        overton_dial=args.overton,
        semantic_delta=args.semantic,
        content_filter=args.filter,
        emotional_state=args.state,
        country_override=args.country,
        topic_override=args.topic,
        no_print=args.no_print,
        no_image=args.no_image,
        printer_port=args.printer_port,
    )

if __name__ == "__main__":
    main()
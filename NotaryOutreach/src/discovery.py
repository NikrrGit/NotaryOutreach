"""Discover notary candidates via Groq's web-search-enabled model."""
 
import argparse
import json
import sys
from pathlib import Path
 
from groq import APIError, Groq
 
from .config import ConfigurationError, Settings, load_config
from .models import Candidate, ValidationError
 
MODEL = "groq/compound"  # Groq's agentic model with built-in web search.
 
SYSTEM_PROMPT = (
    "You find real, currently practicing notaries in a given city. "
    "Search the web and return only verifiable, real businesses. "
    "Respond with a JSON array only, no prose, no markdown fences. "
    "Each item: {name, city, source_url, website, email, phone}. "
    "Use null for any field you cannot verify. Do not invent data."
)


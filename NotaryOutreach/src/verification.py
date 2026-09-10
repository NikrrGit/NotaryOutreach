"""Assess UG-formation suitability for a candidate from its website content."""
 
import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
 
from groq import APIError, Groq
 
from .config import ConfigurationError
from .discovery import build_client
from .models import Candidate, CandidateStatus, ValidationError
 
MODEL = "llama-3.3-70b-versatile"
FETCH_TIMEOUT = 15
MAX_PAGE_CHARS = 6000
TAG_RE = re.compile(r"<[^>]+>")
 
SYSTEM_PROMPT = (
    "You assess whether a notary's website shows evidence they support UG "
    "(Unternehmergesellschaft) and GmbH company formation, Gesellschaftsrecht. "
    "Base your judgment only on the provided page text, not assumptions. "
    "Respond with a JSON object only, no prose, no markdown fences: "
    '{"supported": true/false/null, "confidence": 0.0-1.0, "reason": string}. '
    "Use null for supported if the page gives no relevant evidence either way."
)
 
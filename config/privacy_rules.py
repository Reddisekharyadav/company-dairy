"""
Privacy rules and text scrubbing module.
Implements privacy-safe exclusion rules for screen capture and redaction of sensitive text.
"""
import os
import json
import re
from pathlib import Path
from typing import Dict, List, Any

DEFAULT_PROCESS_BLACKLIST = [
    'keepass', 'bitwarden', '1password', 'lastpass', 'keychain', 
    'credential', 'mstsc'
]

DEFAULT_WINDOW_TITLE_BLACKLIST = [
    'password', 'bank', 'credit card', 'social security', 'ssn', 
    'pin code', 'sign in', 'login', 'incognito', 'private browsing', 'inprivate'
]

class PrivacyRules:
    """
    Manages privacy rules for screen capture.
    """
    def __init__(self):
        appdata_dir = os.environ.get('APPDATA')
        if not appdata_dir:
            # Fallback for systems without APPDATA or when running outside typical Windows env
            appdata_dir = str(Path.home() / 'AppData' / 'Roaming')
        
        self.config_dir = Path(appdata_dir) / 'WorkSense'
        self.config_file = self.config_dir / 'privacy_rules.json'
        
        self.process_blacklist: List[str] = list(DEFAULT_PROCESS_BLACKLIST)
        self.window_title_patterns: List[str] = list(DEFAULT_WINDOW_TITLE_BLACKLIST)
        
        self._load_or_create()

    def _load_or_create(self) -> None:
        """Loads rules from config file or creates it with defaults if it doesn't exist."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.process_blacklist = data.get('process_blacklist', self.process_blacklist)
                self.window_title_patterns = data.get('window_title_patterns', self.window_title_patterns)
            except (json.JSONDecodeError, OSError):
                # If error reading/parsing, fallback to defaults and save them
                self.save()
        else:
            self.save()

    def should_capture(self, process_name: str, window_title: str) -> bool:
        """
        Determines whether a window should be captured based on its process name and title.
        Returns False if the app/window matches any blacklist rule, True otherwise.
        """
        process_name_lower = process_name.lower()
        window_title_lower = window_title.lower()

        for proc in self.process_blacklist:
            if proc.lower() in process_name_lower:
                return False

        for pattern in self.window_title_patterns:
            try:
                if re.search(pattern, window_title_lower, re.IGNORECASE):
                    return False
            except re.error:
                # If regex compilation fails, fallback to simple string matching
                if pattern.lower() in window_title_lower:
                    return False

        return True

    def add_process_rule(self, process_name: str) -> None:
        """
        Adds a new process to the blacklist.
        """
        if process_name not in self.process_blacklist:
            self.process_blacklist.append(process_name)
            self.save()

    def add_title_pattern(self, pattern: str) -> None:
        """
        Adds a new regex pattern to the window title blacklist.
        """
        if pattern not in self.window_title_patterns:
            self.window_title_patterns.append(pattern)
            self.save()

    def save(self) -> None:
        """
        Persists current rules to disk.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            'process_blacklist': self.process_blacklist,
            'window_title_patterns': self.window_title_patterns
        }
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    def get_rules(self) -> Dict[str, List[str]]:
        """
        Returns the current rules as a dictionary for the API.
        """
        return {
            'process_blacklist': self.process_blacklist,
            'window_title_patterns': self.window_title_patterns
        }


def scrub_sensitive_text(text: str) -> str:
    """
    Redacts sensitive information like credit card numbers, SSNs, and emails from text.
    Replaces matches with '[REDACTED]'.
    """
    if not text:
        return text

    # Regex for 16-digit credit card number with optional separators (space or dash)
    cc_pattern = r'\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b'
    
    # Regex for SSN: XXX-XX-XXXX
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    
    # Regex for Email
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    
    # Apply redactions
    redacted_text = text
    redacted_text = re.sub(email_pattern, '[REDACTED]', redacted_text)
    redacted_text = re.sub(ssn_pattern, '[REDACTED]', redacted_text)
    redacted_text = re.sub(cc_pattern, '[REDACTED]', redacted_text)
    
    return redacted_text

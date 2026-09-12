"""
Screen Analyzer — generates intelligent notes from OCR text, 100% locally.

No external API keys needed. Uses keyword extraction, pattern matching,
and heuristic classification to determine what the user is doing on screen.

Example output:
  "Reading Python documentation about asyncio event loops"
  "Coding in VS Code — editing tracker.py (Python)"
  "In a Teams meeting: Sprint Planning Review"
  "Browsing Reddit — r/programming thread about Rust vs Go"
  "Writing an email in Gmail to project-team@company.com"
"""
import re
import logging
from collections import Counter
from typing import Optional

log = logging.getLogger('screen_analyzer')

# ── Stop words for keyword extraction ─────────────────────────────────────────
STOP_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'both', 'either',
    'neither', 'each', 'every', 'all', 'any', 'few', 'more', 'most',
    'other', 'some', 'such', 'no', 'only', 'own', 'same', 'than',
    'too', 'very', 'just', 'because', 'if', 'when', 'while', 'that',
    'this', 'these', 'those', 'it', 'its', 'my', 'your', 'his', 'her',
    'our', 'their', 'what', 'which', 'who', 'whom', 'whose', 'where',
    'how', 'new', 'tab', 'page', 'file', 'edit', 'view', 'help', 'window',
    'menu', 'close', 'open', 'save', 'copy', 'paste', 'cut', 'undo',
    'click', 'type', 'enter', 'select', 'home', 'end', 'insert', 'delete',
    'search', 'find', 'replace', 'print', 'zoom', 'scroll', 'settings',
    'options', 'tools', 'format', 'about', 'blank', 'untitled',
})


def _detect_coding(ocr_text, proc, title):
    """Detect if the user is coding and return a description."""
    code_indicators = [
        r'def\s+\w+\s*\(', r'class\s+\w+', r'import\s+\w+', r'from\s+\w+\s+import',
        r'function\s+\w+', r'const\s+\w+', r'let\s+\w+', r'var\s+\w+',
        r'public\s+(?:class|void|static)', r'private\s+\w+', r'return\s+',
        r'if\s*\(', r'for\s*\(', r'while\s*\(',
        r'console\.log', r'print\(', r'System\.out',
        r'#include', r'using\s+namespace', r'package\s+\w+',
        r'\}\s*catch\s*\(', r'try\s*\{', r'except\s+\w+',
    ]

    ide_names = {
        'code.exe': 'VS Code', 'code': 'VS Code',
        'devenv.exe': 'Visual Studio', 'devenv': 'Visual Studio',
        'pycharm': 'PyCharm', 'pycharm64.exe': 'PyCharm',
        'idea64.exe': 'IntelliJ', 'idea': 'IntelliJ',
        'webstorm': 'WebStorm', 'cursor.exe': 'Cursor', 'cursor': 'Cursor',
        'sublime_text.exe': 'Sublime Text', 'notepad++.exe': 'Notepad++',
        'vim': 'Vim', 'nvim': 'Neovim', 'emacs': 'Emacs',
    }

    proc_lower = (proc or '').lower()
    ide = None
    for key, name in ide_names.items():
        if key in proc_lower:
            ide = name
            break

    code_score = sum(1 for p in code_indicators if re.search(p, ocr_text))

    if code_score >= 2 or ide:
        file_match = re.search(r'(\w+\.(?:py|js|ts|java|cpp|c|go|rs|rb|php|html|css|jsx|tsx|vue|svelte))', title or '')
        lang_map = {
            '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
            '.java': 'Java', '.cpp': 'C++', '.c': 'C', '.go': 'Go',
            '.rs': 'Rust', '.rb': 'Ruby', '.php': 'PHP',
            '.html': 'HTML', '.css': 'CSS',
        }
        file_name = file_match.group(1) if file_match else None
        lang = None
        if file_name:
            ext = '.' + file_name.rsplit('.', 1)[-1]
            lang = lang_map.get(ext)

        parts = []
        parts.append(f"Coding in {ide}" if ide else "Writing code")
        if file_name:
            parts.append(f"editing {file_name}")
        if lang:
            return f"{' — '.join(parts)} ({lang})"
        return ' — '.join(parts)

    return None


def _detect_meeting(ocr_text, proc, title):
    """Detect if user is in a video/voice meeting."""
    full = f"{proc or ''} {title or ''} {ocr_text[:500]}".lower()

    meeting_patterns = [
        (r'microsoft teams.*(?:meeting|call)', 'Teams'),
        (r'teams.*(?:meeting|call|join)', 'Teams'),
        (r'zoom\s*(?:meeting|call|webinar)', 'Zoom'),
        (r'google\s*meet', 'Google Meet'),
        (r'webex', 'Webex'),
        (r'slack.*(?:huddle|call)', 'Slack'),
        (r'discord.*(?:voice|call)', 'Discord'),
    ]

    for pattern, platform in meeting_patterns:
        if re.search(pattern, full):
            title_clean = (title or '').strip()
            for suffix in ['- Microsoft Teams', '- Zoom', '- Google Chrome', '| Microsoft Teams']:
                title_clean = title_clean.replace(suffix, '').strip()
            if title_clean and len(title_clean) > 3:
                return f"In a {platform} meeting: {title_clean[:80]}"
            return f"In a {platform} meeting"

    mute_indicators = ['mute', 'unmute', 'camera off', 'camera on', 'share screen',
                       'participants', 'raise hand', 'leave meeting', 'end call']
    mute_score = sum(1 for m in mute_indicators if m in full)
    if mute_score >= 2:
        return "In a video/voice call"
    return None


def _detect_browsing(ocr_text, proc, title):
    """Detect web browsing activity and describe the content."""
    browsers = ['chrome', 'firefox', 'edge', 'opera', 'brave', 'safari', 'vivaldi']
    proc_lower = (proc or '').lower()

    is_browser = any(b in proc_lower for b in browsers)
    if not is_browser:
        return None

    full = f"{title or ''} {ocr_text[:800]}".lower()

    site_patterns = [
        (r'stackoverflow|stack overflow', 'Researching a coding problem on Stack Overflow'),
        (r'github\.com', 'Reviewing code/issues on GitHub'),
        (r'gitlab', 'Working in GitLab'),
        (r'chatgpt|chat\.openai', 'Using ChatGPT AI assistant'),
        (r'claude\.ai|anthropic', 'Using Claude AI assistant'),
        (r'gemini\.google', 'Using Google Gemini AI'),
        (r'youtube\.com', 'Watching a YouTube video'),
        (r'reddit\.com|/r/', 'Browsing Reddit'),
        (r'linkedin\.com', 'Browsing LinkedIn'),
        (r'twitter\.com|x\.com', 'Browsing Twitter/X'),
        (r'wikipedia', 'Reading a Wikipedia article'),
        (r'docs\.(?:python|google|microsoft|aws|rust)', 'Reading technical documentation'),
        (r'medium\.com', 'Reading an article on Medium'),
        (r'dev\.to', 'Reading a dev.to article'),
        (r'udemy|coursera|edx|pluralsight', 'Taking an online course'),
        (r'mail\.google|gmail', 'Checking Gmail'),
        (r'outlook\.(?:com|live)', 'Checking Outlook email'),
        (r'slack\.com', 'Using Slack messaging'),
        (r'figma\.com', 'Working on a Figma design'),
        (r'notion\.so', 'Working in Notion'),
        (r'jira|atlassian', 'Managing tasks in Jira'),
        (r'trello', 'Managing boards in Trello'),
        (r'amazon\.|flipkart|shopping', 'Shopping online'),
        (r'news|bbc|cnn|reuters', 'Reading news'),
    ]

    for pattern, description in site_patterns:
        if re.search(pattern, full):
            clean_title = (title or '').strip()
            for suffix in ['- Google Chrome', '- Microsoft Edge', '- Firefox', '- Opera', '- Brave', '- Google Search']:
                clean_title = clean_title.replace(suffix, '').strip()
            parts = re.split(r'\s[-\u2013\u2014|]\s', clean_title)
            page_topic = parts[0].strip()[:80] if parts else None
            if page_topic and len(page_topic) > 5:
                return f"{description}: \"{page_topic}\""
            return description

    clean_title = (title or '').strip()
    for suffix in ['- Google Chrome', '- Microsoft Edge', '- Firefox', '- Opera', '- Brave']:
        clean_title = clean_title.replace(suffix, '').strip()

    if clean_title and len(clean_title) > 5:
        search_match = re.search(r'(.+?)\s*[-\u2013]\s*(?:google search|bing|duckduckgo)', clean_title, re.I)
        if search_match:
            return f"Searching the web for: \"{search_match.group(1).strip()[:60]}\""
        return f"Browsing: \"{clean_title[:80]}\""
    return "Browsing the web"


def _detect_document_work(ocr_text, proc, title):
    """Detect document editing (Word, Excel, PowerPoint, etc.)."""
    proc_lower = (proc or '').lower()

    doc_apps = {
        'winword': ('Microsoft Word', 'Writing document'),
        'excel': ('Microsoft Excel', 'Working on spreadsheet'),
        'powerpnt': ('Microsoft PowerPoint', 'Working on presentation'),
        'onenote': ('Microsoft OneNote', 'Taking notes'),
        'libreoffice': ('LibreOffice', 'Editing document'),
        'soffice': ('LibreOffice', 'Editing document'),
        'acrord32': ('Adobe Reader', 'Reading PDF'),
        'acrobat': ('Adobe Acrobat', 'Working on PDF'),
    }

    for key, (app_name, action) in doc_apps.items():
        if key in proc_lower:
            clean_title = (title or '').strip()
            for suffix in [f' - {app_name}', ' - Microsoft Word', ' - Microsoft Excel',
                           ' - Microsoft PowerPoint']:
                clean_title = clean_title.replace(suffix, '').strip()
            if clean_title and len(clean_title) > 3:
                return f"{action} in {app_name}: \"{clean_title[:60]}\""
            return f"{action} in {app_name}"

    if '.pdf' in (title or '').lower():
        pdf_name = re.search(r'([\w\s.-]+\.pdf)', title or '', re.I)
        if pdf_name:
            return f"Reading PDF: \"{pdf_name.group(1)[:60]}\""
    return None


def _detect_terminal(ocr_text, proc, title):
    """Detect terminal/command line usage."""
    terminal_apps = ['cmd.exe', 'powershell', 'pwsh', 'windowsterminal',
                     'terminal', 'iterm', 'konsole', 'gnome-terminal',
                     'alacritty', 'wezterm', 'hyper', 'mintty', 'bash', 'zsh']

    proc_lower = (proc or '').lower()
    if any(t in proc_lower for t in terminal_apps):
        cmd_patterns = [
            (r'(?:npm|yarn|pnpm)\s+(?:run|install|build|start|test)', 'Running npm/yarn commands'),
            (r'(?:pip|pip3)\s+install', 'Installing Python packages'),
            (r'docker\s+(?:build|run|compose|pull)', 'Running Docker commands'),
            (r'git\s+(?:commit|push|pull|merge|rebase|log|diff)', 'Using Git'),
            (r'python|python3|py\s', 'Running Python scripts'),
            (r'node\s', 'Running Node.js'),
            (r'cargo\s', 'Running Rust Cargo'),
            (r'make\s|cmake', 'Running build commands'),
            (r'ssh\s', 'Connected via SSH'),
            (r'kubectl|helm', 'Managing Kubernetes'),
        ]
        for pattern, desc in cmd_patterns:
            if re.search(pattern, ocr_text[:500], re.I):
                return desc
        return "Working in terminal/command line"
    return None


def _extract_top_keywords(text, n=5):
    """Extract the top N keywords from text."""
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    words = [w for w in words if w not in STOP_WORDS]
    if not words:
        return []
    counter = Counter(words)
    return [w for w, _ in counter.most_common(n)]


def analyze_screen(ocr_text: str, proc_name: str, window_title: str) -> Optional[str]:
    """
    Analyze OCR text + active window info and generate a human-readable note.

    Returns a 1-2 sentence summary of what the user is doing, or None if
    the screen content is too noisy/empty to produce a meaningful summary.
    """
    if not ocr_text or len(ocr_text.strip()) < 20:
        return None

    detectors = [
        _detect_meeting,
        _detect_coding,
        _detect_document_work,
        _detect_terminal,
        _detect_browsing,
    ]

    for detector in detectors:
        try:
            result = detector(ocr_text, proc_name, window_title)
            if result:
                return result
        except Exception as e:
            log.debug('Detector %s failed: %s', detector.__name__, e)

    # Fallback: use window title + top keywords
    keywords = _extract_top_keywords(ocr_text)
    if window_title and len(window_title) > 3:
        clean_title = window_title.strip()[:80]
        app_name = proc_name or 'unknown app'
        if keywords:
            return f"Active in {app_name}: \"{clean_title}\" (topics: {', '.join(keywords[:3])})"
        return f"Active in {app_name}: \"{clean_title}\""

    if keywords:
        return f"Working on: {', '.join(keywords[:4])}"
    return None

"""
URL/website name extractor and activity categorizer.
Parses browser window titles to get real site names and categorizes all activity.
"""
import re
from typing import Optional, Tuple

# Map domain keywords → human-readable site name
DOMAIN_MAP = {
    'whatsapp': 'WhatsApp',
    'web.whatsapp': 'WhatsApp',
    'instagram': 'Instagram',
    'facebook': 'Facebook',
    'twitter': 'Twitter',
    'x.com': 'X (Twitter)',
    'youtube': 'YouTube',
    'netflix': 'Netflix',
    'reddit': 'Reddit',
    'github': 'GitHub',
    'stackoverflow': 'Stack Overflow',
    'gitlab': 'GitLab',
    'bitbucket': 'Bitbucket',
    'google': 'Google',
    'gmail': 'Gmail',
    'google docs': 'Google Docs',
    'google sheets': 'Google Sheets',
    'google meet': 'Google Meet',
    'meet.google': 'Google Meet',
    'docs.google': 'Google Docs',
    'sheets.google': 'Google Sheets',
    'linkedin': 'LinkedIn',
    'medium': 'Medium',
    'dev.to': 'Dev.to',
    'weather': 'Weather',
    'weather.com': 'Weather.com',
    'news': 'News',
    'wikipedia': 'Wikipedia',
    'chatgpt': 'ChatGPT',
    'openai': 'OpenAI',
    'gemini': 'Gemini AI',
    'claude': 'Claude AI',
    'notion': 'Notion',
    'trello': 'Trello',
    'jira': 'Jira',
    'slack': 'Slack',
    'discord': 'Discord',
    'telegram': 'Telegram',
    'zoom': 'Zoom',
    'teams': 'Microsoft Teams',
    'outlook': 'Outlook',
    'office': 'Microsoft Office',
    'amazon': 'Amazon',
    'flipkart': 'Flipkart',
    'leetcode': 'LeetCode',
    'hackerrank': 'HackerRank',
    'udemy': 'Udemy',
    'coursera': 'Coursera',
    'npm': 'npm',
    'pypi': 'PyPI',
    'localhost': 'Local Server',
    '127.0.0.1': 'Local Server',
}

# Category mapping: (process_name_keywords, title_keywords) → category
CATEGORY_RULES = [
    # Coding
    (['code.exe', 'pycharm', 'intellij', 'idea', 'webstorm', 'rider',
      'vim', 'nvim', 'sublime', 'atom', 'cursor', 'antigravity'],
     [], 'Coding'),

    # Coding via browser
    (['chrome.exe', 'msedge.exe', 'firefox.exe'],
     ['github', 'gitlab', 'stackoverflow', 'stack overflow', 'leetcode',
      'hackerrank', 'codepen', 'replit', 'codesandbox', 'npm', 'pypi',
      'documentation', 'docs.', 'api reference', 'localhost', '127.0.0.1',
      'worksense'],
     'Coding/Research'),

    # Communication
    (['chrome.exe', 'msedge.exe', 'firefox.exe'],
     ['whatsapp', 'slack', 'discord', 'telegram', 'gmail', 'outlook',
      'teams', 'meet', 'zoom', 'email'],
     'Communication'),

    # Communication apps
    (['slack.exe', 'discord.exe', 'telegram.exe', 'msteams.exe',
      'whatsapp.exe'],
     [], 'Communication'),

    # Meetings (desktop apps)
    (['teams.exe', 'ms-teams.exe', 'zoom.exe', 'webex.exe'],
     ['meeting', 'call', 'join'], 'Meeting'),

    # Meetings (browser)
    (['chrome.exe', 'msedge.exe', 'firefox.exe'],
     ['meet.google.com', 'teams.microsoft.com', 'zoom.us', 'webex.com'],
     'Meeting'),

    # Entertainment
    (['chrome.exe', 'msedge.exe', 'firefox.exe'],
     ['youtube', 'netflix', 'spotify', 'amazon prime', 'hotstar',
      'reddit', 'instagram', 'facebook', 'twitter', 'x.com'],
     'Entertainment/Social'),

    # Productivity
    (['chrome.exe', 'msedge.exe', 'firefox.exe'],
     ['notion', 'trello', 'jira', 'asana', 'google docs', 'google sheets',
      'google drive', 'figma', 'canva'],
     'Productivity'),

    (['excel.exe', 'winword.exe', 'powerpnt.exe', 'onenote.exe',
      'notepad.exe', 'notepad++.exe'],
     [], 'Productivity'),

    # Learning
    (['chrome.exe', 'msedge.exe', 'firefox.exe'],
     ['udemy', 'coursera', 'medium', 'dev.to', 'wikipedia', 'tutorial',
      'learn', 'course', 'lecture', 'gemini', 'chatgpt', 'claude', 'openai'],
     'Learning/Research'),

    # Browsing (general)
    (['chrome.exe', 'msedge.exe', 'firefox.exe'],
     ['weather', 'news', 'bbc', 'google search', 'bing'],
     'Browsing'),

    # System
    (['explorer.exe', 'cmd.exe', 'powershell.exe', 'windowsterminal.exe',
      'taskmgr.exe', 'regedit.exe'],
     [], 'System'),
]


def extract_website_name(window_title: str, process_name: str) -> Optional[str]:
    """Extract a clean website name from a browser window title."""
    if not window_title:
        return None

    proc = (process_name or '').lower()
    is_browser = any(b in proc for b in ('chrome', 'msedge', 'firefox', 'opera', 'brave'))
    if not is_browser:
        return None

    title_lower = window_title.lower()

    # Check domain map
    for keyword, name in DOMAIN_MAP.items():
        if keyword.lower() in title_lower:
            return name

    # Try to extract site from "Page Title - Site Name - Browser" pattern
    parts = [p.strip() for p in re.split(r'\s[-–—]\s', window_title) if p.strip()]
    # The last part before browser name is usually the site
    browser_names = ['Google Chrome', 'Microsoft Edge', 'Firefox', 'Opera', 'Brave']
    filtered = [p for p in parts if not any(b.lower() in p.lower() for b in browser_names)]
    if filtered:
        # Return the last part (usually site name)
        site = filtered[-1]
        if len(site) < 60:  # reasonable length for a site name
            return site

    return None


def categorize_activity(process_name: str, window_title: str) -> str:
    """Return a category string for the given activity."""
    proc = (process_name or '').lower()
    title = (window_title or '').lower()

    for proc_keywords, title_keywords, category in CATEGORY_RULES:
        proc_match = not proc_keywords or any(k in proc for k in proc_keywords)
        title_match = not title_keywords or any(k in title for k in title_keywords)
        if proc_match and title_match:
            return category

    # Default
    if any(b in proc for b in ('chrome', 'msedge', 'firefox')):
        return 'Browsing'
    return 'Other'

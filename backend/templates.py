from jinja2 import Environment, FileSystemLoader
import os
import sys


def get_template_dir() -> str:
    candidates = []
    if hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, 'backend', 'templates'))
        candidates.append(os.path.join(sys._MEIPASS, 'templates'))
    candidates.append(os.path.join(os.path.dirname(__file__), 'templates'))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
    candidates.append(os.path.join(os.getcwd(), 'backend', 'templates'))
    for c in candidates:
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, 'index.html')):
            return c
    return candidates[0] if candidates else os.path.join(os.path.dirname(__file__), 'templates')


template_dir = get_template_dir()
env = Environment(loader=FileSystemLoader(template_dir))


def render_index():
    global env, template_dir
    if not os.path.isfile(os.path.join(template_dir, 'index.html')):
        template_dir = get_template_dir()
        env = Environment(loader=FileSystemLoader(template_dir))
    tpl = env.get_template("index.html")
    return tpl.render()

from jinja2 import Environment, FileSystemLoader
import os
import sys


def resource_path(path: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, path)
    return os.path.join(os.path.dirname(__file__), path)


template_dir = resource_path(os.path.join('backend', 'templates')) if hasattr(sys, '_MEIPASS') else os.path.join(os.path.dirname(__file__), 'templates')
env = Environment(loader=FileSystemLoader(template_dir))


def render_index():
    tpl = env.get_template("index.html")
    return tpl.render()

"""Shared Jinja2Templates instance with global context variables."""
from datetime import datetime
from fastapi.templating import Jinja2Templates

from version_utils import read_version

templates = Jinja2Templates(directory="templates")
templates.env.globals["now"] = datetime.utcnow()
templates.env.globals["app_version"] = read_version()

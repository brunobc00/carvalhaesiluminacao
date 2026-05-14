"""Shared Jinja2Templates instance with global context variables."""
from datetime import datetime
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
templates.env.globals["now"] = datetime.utcnow()

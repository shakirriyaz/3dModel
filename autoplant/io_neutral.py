from __future__ import annotations
import json
from .model import Project


def load_project(path_or_dict) -> Project:
    if isinstance(path_or_dict, dict):
        return Project(**path_or_dict)
    with open(path_or_dict) as f:
        return Project(**json.load(f))

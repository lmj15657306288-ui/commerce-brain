from __future__ import annotations

import json

from control_plane.app import create_app


print(json.dumps(create_app().openapi(), ensure_ascii=False, separators=(",", ":")))

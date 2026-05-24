import re
import yaml

_FM_PATTERN = re.compile(r'^---\r?\n(.*?)\r?\n---\r?\n', re.DOTALL)


def extract(text: str) -> tuple[dict, str]:
    """Return (meta_dict, body) stripping the YAML front-matter block if present."""
    m = _FM_PATTERN.match(text)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except Exception:
            meta = {}
        return meta, text[m.end():]
    return {}, text


def inject(meta: dict, body: str) -> str:
    """Prepend a YAML front-matter block to body. Returns body unchanged if meta is empty."""
    if not meta:
        return body
    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=True).strip()
    return f"---\n{fm}\n---\n{body}"

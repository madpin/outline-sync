import os
import re
import urllib.parse
import requests

ATTACHMENT_DIR = "_attachments"

# Matches both ![alt](url) image links and [label](url) regular links
ATTACH_PATTERN = re.compile(r'(!?\[[^\]]*\]\()([^)\s]+)(\))')

MIME_TO_EXT: dict[str, str] = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/svg+xml': '.svg',
    'image/bmp': '.bmp',
    'image/tiff': '.tif',
    'application/pdf': '.pdf',
    'application/zip': '.zip',
    'text/plain': '.txt',
    'text/csv': '.csv',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'application/msword': '.doc',
    'application/vnd.ms-excel': '.xls',
}
EXT_TO_MIME: dict[str, str] = {ext: mime for mime, ext in MIME_TO_EXT.items()}


def _attachment_id(url: str) -> str | None:
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    ids = params.get('id', [])
    return ids[0] if ids else None


def _is_outline_url(url: str, base_url: str) -> bool:
    return (
        'attachments.redirect' in url
        or url.startswith(base_url + '/api/')
        or url.startswith('/api/')
    )


def _is_pushable_local_path(url: str, collection_path: str) -> bool:
    """True only for local paths that point to real attachment files (not .md or unknown types)."""
    if any(url.startswith(p) for p in ('http://', 'https://', 'data:', '#', '/')):
        return False

    local_path = os.path.join(collection_path, url.replace('/', os.sep))
    if not os.path.exists(local_path) or not os.path.isfile(local_path):
        return False

    # Must be inside _attachments/ OR have a known non-markdown extension
    parts = url.replace('\\', '/').split('/')
    if ATTACHMENT_DIR in parts:
        return True

    ext = os.path.splitext(local_path)[1].lower()
    return ext in EXT_TO_MIME and ext != '.md'


def _filename_from_response(resp: requests.Response, att_id: str | None) -> str:
    cd = resp.headers.get('Content-Disposition', '')
    if 'filename=' in cd:
        name = cd.split('filename=')[-1].strip().strip('"\'')
        if name:
            return name
    ct = resp.headers.get('Content-Type', '').split(';')[0].strip()
    ext = MIME_TO_EXT.get(ct, '')
    return (att_id or 'attachment') + ext


def pull_attachments(text: str, collection_path: str, base_url: str, headers: dict) -> str:
    """Download Outline-hosted attachments to _attachments/ and rewrite URLs to relative paths."""
    att_dir = os.path.join(collection_path, ATTACHMENT_DIR)

    def replace(match):
        prefix, url, suffix = match.group(1), match.group(2), match.group(3)
        if not _is_outline_url(url, base_url):
            return match.group(0)

        full_url = url if url.startswith('http') else base_url + url
        att_id = _attachment_id(full_url)

        try:
            # Use only Authorization header — accept:application/json breaks binary downloads
            dl_headers = {'Authorization': headers.get('Authorization', '')}
            resp = requests.get(full_url, headers=dl_headers, allow_redirects=True, timeout=30)
            if resp.status_code != 200:
                return match.group(0)

            filename = _filename_from_response(resp, att_id)
            os.makedirs(att_dir, exist_ok=True)
            local_path = os.path.join(att_dir, filename)
            if not os.path.exists(local_path):
                with open(local_path, 'wb') as f:
                    f.write(resp.content)

            return prefix + ATTACHMENT_DIR + '/' + filename + suffix
        except Exception:
            return match.group(0)

    return ATTACH_PATTERN.sub(replace, text)


def push_attachments(text: str, collection_path: str, upload_fn) -> str:
    """Upload local attachments to Outline and rewrite paths to Outline attachment URLs."""

    def replace(match):
        prefix, url, suffix = match.group(1), match.group(2), match.group(3)
        if not _is_pushable_local_path(url, collection_path):
            return match.group(0)

        local_path = os.path.join(collection_path, url.replace('/', os.sep))
        try:
            with open(local_path, 'rb') as f:
                file_data = f.read()
            name = os.path.basename(local_path)
            ext = os.path.splitext(name)[1].lower()
            content_type = EXT_TO_MIME.get(ext, 'application/octet-stream')
            outline_url = upload_fn(name=name, content_type=content_type, size=len(file_data), file_data=file_data)
            if outline_url:
                return prefix + outline_url + suffix
        except Exception:
            pass
        return match.group(0)

    return ATTACH_PATTERN.sub(replace, text)

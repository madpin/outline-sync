import os
import requests

from abc import ABC, abstractmethod

from .constants import *
from ..console import logger

class Client(ABC):
    ...

class RemoteClient(Client):
    @abstractmethod
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        if self.verbose:
            self.log = logger()
        self.key = os.getenv("OUTLINE_API_TOKEN")
        self.base_url = os.getenv("OUTLINE_BASE_URL")
        self.headers = {
            'Authorization': 'Bearer ' + os.getenv('OUTLINE_API_TOKEN'),
            'accept': 'application/json',
        }

    def _make_request(self, request_type: RequestType, json_data: dict) -> requests.Response:
        response = requests.post(self.base_url + request_type.value, headers=self.headers, json=json_data)
        return response

    def _upload_attachment(self, name: str, content_type: str, size: int, file_data: bytes) -> str | None:
        """Upload a file to Outline and return its full attachment URL, or None on failure."""
        resp = self._make_request(RequestType.CREATE_ATTACHMENT, json_data={
            "name": name,
            "contentType": content_type,
            "size": size,
        })
        if not resp.ok:
            return None

        data = resp.json().get('data', {})
        upload_path = data.get('uploadUrl')
        form_fields = data.get('form', {})
        att_url = data.get('attachment', {}).get('url')

        if not upload_path or not att_url:
            return None

        upload_url = self.base_url + upload_path if upload_path.startswith('/') else upload_path
        fields = {k: v for k, v in form_fields.items() if k not in ('maxUploadSize',)}
        files = {'file': (name, file_data, content_type)}
        upload_resp = requests.post(
            upload_url,
            headers={'Authorization': f'Bearer {self.key}'},
            data=fields,
            files=files,
        )
        if not upload_resp.ok:
            return None

        return self.base_url + att_url if att_url.startswith('/') else att_url
    
class LocalClient(Client):
    @abstractmethod
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        if self.verbose:
            self.log = logger()
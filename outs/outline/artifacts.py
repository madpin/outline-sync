from typing import List
from .constants import *


class Document():
    def __init__(self,
                 id: str,
                 name: str,
                 parent_collection,
                 mod_date: str,
                 local_path: str | None = None,
                 parent_id: str = '',
                 ) -> None:
        self.id = id
        self.name = name
        self.parent_collection = parent_collection
        self.mod_date = mod_date
        self.local_path = local_path   # absolute path on disk; only set for local documents
        self.parent_id = parent_id     # Outline parent document ID (empty = root of collection)


class Collection():
    def __init__(self,
                 id: str,
                 name: str,
                 documents: List[Document],
                 ) -> None:
        self.id = id
        self.name = name
        self.documents = documents

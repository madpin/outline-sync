import hashlib
import json
import os
import datetime
import shutil
import sys

from ..console import console, logger
from .client import RemoteClient, LocalClient
from .constants import *
from .artifacts import *
from .utils import *
from .attachments import pull_attachments, push_attachments
from .frontmatter import extract as fm_extract, inject as fm_inject


def _parse_outline_dt(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ")


def _content_hash(body: str) -> str:
    """SHA-256 of the markdown body as stored locally (with _attachments/ paths)."""
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _read_meta(path: str) -> dict:
    """Read front-matter from a file; return empty dict on any error."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            meta, _ = fm_extract(f.read())
        return meta
    except Exception:
        return {}


class OutlineClient(RemoteClient):
    """Remote Outline client."""
    def __init__(self, verbose: bool) -> None:
        super().__init__(verbose)
        if self.verbose:
            self.log.info("Building remote client")
        self.__set_library()

    def __set_library(self) -> None:
        self.collections = self._get_client_collections()
        self._get_client_documents()

    def _refresh_client(self) -> None:
        if self.verbose:
            self.log.info("Refreshed remote client")
        self.__set_library()

    def _get_client_collections(self) -> List[Collection]:
        data = json.loads(self._make_request(RequestType.LIST_COLLECTIONS, json_data={
            'offset': 0, 'limit': 100,
        }).text)
        try:
            return [Collection(id=c['id'], name=c['name'], documents=[]) for c in data['data']]
        except KeyError:
            sys.exit(1)

    def _get_client_documents(self) -> None:
        for collection in self.collections:
            data = json.loads(self._make_request(RequestType.LIST_DOCUMENTS, json_data={
                'offset': 0, 'limit': 100,
                'sort': 'updatedAt', 'direction': 'DESC',
                'collectionId': collection.id,
            }).text)['data']

            # Two passes: first collect raw, then mark which docs have children
            raw = [
                {
                    'id': d['id'],
                    'title': d['title'],
                    'updatedAt': d['updatedAt'],
                    'parentDocumentId': d.get('parentDocumentId') or '',
                }
                for d in data
            ]
            parent_ids = {d['parentDocumentId'] for d in raw if d['parentDocumentId']}

            for d in raw:
                collection.documents.append(Document(
                    id=d['id'],
                    name=d['title'],
                    parent_collection=collection,
                    mod_date=d['updatedAt'],
                    parent_id=d['parentDocumentId'],
                ))


class Outline(LocalClient):
    """Local Outline sync manager."""
    def __init__(self, client: RemoteClient, excluded: List[str], path: str, verbose: bool) -> None:
        super().__init__(verbose)
        if self.verbose:
            self.log.info("Building local client")
        self.client = client
        self.path = path
        self.excluded = excluded
        os.makedirs(self.path, exist_ok=True)
        self.__set_library()

    def __set_library(self) -> None:
        self.collections = self._get_local_collections()
        self._get_local_documents()

    def _refresh_local(self) -> None:
        if self.verbose:
            self.log.info("Refreshing local client")
        self.__set_library()

    # ── Local discovery ────────────────────────────────────────────────────

    def _get_local_collections(self) -> List[Collection]:
        collections = []
        for name in os.listdir(self.path):
            if (os.path.isdir(os.path.join(self.path, name))
                    and not name.startswith('.')
                    and not name.startswith('_')
                    and name not in self.excluded):
                collections.append(Collection(id='', name=name, documents=[]))
        return collections

    def _get_local_documents(self) -> None:
        for collection in self.collections:
            self._scan_documents(
                collection,
                os.path.join(self.path, collection.name),
                parent_outline_id='',
            )

    def _scan_documents(self, collection: Collection, folder: str, parent_outline_id: str) -> None:
        """Recursively scan folder for .md files and README.md parent-docs."""
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            return

        for name in entries:
            if name.startswith('.') or name.startswith('_'):
                continue
            full_path = os.path.join(folder, name)

            if os.path.isdir(full_path):
                readme = os.path.join(full_path, 'README.md')
                if os.path.exists(readme):
                    meta = _read_meta(readme)
                    doc_id = meta.get('outline_id', '')
                    collection.documents.append(Document(
                        id=doc_id,
                        name=name,
                        parent_collection=collection,
                        mod_date=meta.get('outline_updated_at', ''),
                        local_path=readme,
                        parent_id=meta.get('outline_parent_id', parent_outline_id),
                    ))
                    self._scan_documents(collection, full_path, doc_id)
                else:
                    self._scan_documents(collection, full_path, parent_outline_id)

            elif name.endswith('.md') and name != 'README.md':
                meta = _read_meta(full_path)
                collection.documents.append(Document(
                    id=meta.get('outline_id', ''),
                    name=os.path.splitext(name)[0],
                    parent_collection=collection,
                    mod_date=meta.get('outline_updated_at', ''),
                    local_path=full_path,
                    parent_id=meta.get('outline_parent_id', parent_outline_id),
                ))

    # ── Path computation for nested documents ─────────────────────────────

    def _local_path_for_client_doc(self, doc: Document, all_docs: List[Document],
                                    collection_path: str) -> str:
        """Compute the local file path for an Outline document, reflecting parent hierarchy.

        Parent docs (those with children) become <name>/README.md.
        Leaf docs become <name>.md inside their parent folder.
        """
        # Build ancestor folder path by walking up the parent chain
        segments: List[str] = []
        current_parent_id = doc.parent_id
        while current_parent_id:
            parent = next((d for d in all_docs if d.id == current_parent_id), None)
            if parent is None:
                break
            segments.insert(0, parent.name)
            current_parent_id = parent.parent_id

        has_children = any(d.parent_id == doc.id for d in all_docs)
        if has_children:
            segments.append(doc.name)
            filename = 'README.md'
        else:
            filename = doc.name + '.md'

        return os.path.join(collection_path, *segments, filename)

    # ── ID-based helpers ───────────────────────────────────────────────────

    def _local_id_map(self) -> dict:
        return {
            doc.id: doc
            for coll in self.collections
            for doc in coll.documents
            if doc.id
        }

    def _local_name_map(self) -> dict:
        """Map (collection_name, doc_name) → local Document for name-based fallback."""
        return {
            (doc.parent_collection.name, doc.name): doc
            for coll in self.collections
            for doc in coll.documents
        }

    def _client_id_set(self) -> set:
        return {doc.id for coll in self.client.collections for doc in coll.documents}

    def _find_local_path_for_id(self, outline_id: str) -> str | None:
        doc = self._local_id_map().get(outline_id)
        return doc.local_path if doc else None

    # ── Status helpers (used by `status` command) ──────────────────────────

    def _get_missing_items(self, sync_type: SyncType) -> List[Collection]:
        if sync_type == SyncType.LOCAL:
            return self._missing_for_local()
        return self._missing_for_remote()

    def _get_old_items(self, sync_type: SyncType) -> List[Collection]:
        if sync_type == SyncType.LOCAL:
            return self._old_for_local()
        return self._old_for_remote()

    # ── Missing items ──────────────────────────────────────────────────────

    def _missing_for_local(self) -> List[Collection]:
        """Outline docs with no local counterpart (matched by outline_id, then by name)."""
        id_map = self._local_id_map()
        name_map = self._local_name_map()
        local_coll_names = {c.name for c in self.collections}
        missing = []

        for client_coll in self.client.collections:
            if client_coll.name not in local_coll_names:
                missing.append(client_coll)
            else:
                missing_docs = []
                for doc in client_coll.documents:
                    if doc.id in id_map:
                        continue  # matched by ID
                    if (client_coll.name, doc.name) in name_map:
                        continue  # matched by name (will be linked on create)
                    missing_docs.append(doc)
                if missing_docs:
                    missing.append(Collection(id=client_coll.id, name=client_coll.name,
                                              documents=missing_docs))
        return missing

    def _missing_for_remote(self) -> List[Collection]:
        """Local docs with no Outline counterpart (no outline_id, or ID gone from Outline)."""
        client_ids = self._client_id_set()
        client_coll_names = {c.name for c in self.client.collections}
        missing = []

        for local_coll in self.collections:
            if local_coll.name not in client_coll_names:
                missing.append(local_coll)
            else:
                missing_docs = [
                    d for d in local_coll.documents
                    if not d.id or d.id not in client_ids
                ]
                if missing_docs:
                    missing.append(Collection(id=local_coll.id, name=local_coll.name,
                                              documents=missing_docs))
        return missing

    # ── Outdated items ─────────────────────────────────────────────────────

    def _old_for_local(self) -> List[Collection]:
        """Outline docs newer than their local counterpart (by outline_updated_at)."""
        id_map = self._local_id_map()
        name_map = self._local_name_map()
        old = []

        for client_coll in self.client.collections:
            old_docs = []
            for client_doc in client_coll.documents:
                local_doc = id_map.get(client_doc.id) or name_map.get(
                    (client_coll.name, client_doc.name)
                )
                if not local_doc or not local_doc.mod_date:
                    continue
                try:
                    if _parse_outline_dt(client_doc.mod_date) > _parse_outline_dt(local_doc.mod_date):
                        old_docs.append(client_doc)
                except ValueError:
                    pass
            if old_docs:
                old.append(Collection(id=client_coll.id, name=client_coll.name, documents=old_docs))
        return old

    def _old_for_remote(self) -> List[Collection]:
        """Local docs whose body hash differs from outline_content_hash (user edited)."""
        client_ids = self._client_id_set()
        old = []

        for local_coll in self.collections:
            old_docs = []
            for local_doc in local_coll.documents:
                if not local_doc.id or local_doc.id not in client_ids:
                    continue
                if not local_doc.local_path:
                    continue
                try:
                    with open(local_doc.local_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    meta, body = fm_extract(text)
                    stored_hash = meta.get('outline_content_hash', '')
                    if stored_hash and _content_hash(body) != stored_hash:
                        old_docs.append(local_doc)
                except Exception:
                    pass
            if old_docs:
                old.append(Collection(id=local_coll.id, name=local_coll.name, documents=old_docs))
        return old

    # ── Create local ───────────────────────────────────────────────────────

    def _create_local_collections(self, collections: List[Collection]) -> None:
        for collection in collections:
            os.makedirs(os.path.join(self.path, collection.name), exist_ok=True)
        self._refresh_local()

    def _create_local_documents(self, client_collection: Collection) -> None:
        collection_path = os.path.join(self.path, client_collection.name)
        all_docs = client_collection.documents  # needed for path computation

        for document in client_collection.documents:
            data = json.loads(self.client._make_request(
                RequestType.RETRIEVE_DOCUMENT, json_data={"id": document.id}
            ).text)['data']

            body = pull_attachments(
                data['text'], collection_path, self.client.base_url, self.client.headers
            )

            meta = {
                'outline_id': data['id'],
                'outline_collection_id': client_collection.id,
                'outline_updated_at': data['updatedAt'],
                'outline_content_hash': _content_hash(body),
            }
            if document.parent_id:
                meta['outline_parent_id'] = document.parent_id

            # Prefer existing local path (handles renames); fall back to computed path
            existing = (
                self._find_local_path_for_id(data['id'])
                or self._local_name_map().get((client_collection.name, document.name),
                                              None) and
                self._local_name_map()[(client_collection.name, document.name)].local_path
            )
            local_path = existing or self._local_path_for_client_doc(
                document, all_docs, collection_path
            )
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            with open(local_path, 'w', encoding='utf-8') as f:
                f.write(fm_inject(meta, body))

        self._refresh_local()

    # ── Update local ───────────────────────────────────────────────────────

    def _update_local_documents(self, collection: Collection) -> None:
        self._create_local_documents(collection)

    # ── Create remote ──────────────────────────────────────────────────────

    def _create_client_collections(self, collections: List[Collection]) -> None:
        client_coll_names = {c.name for c in self.client.collections}
        color = os.getenv("COLLECTION_COLOR") or "#FFFFFF"

        for collection in collections:
            if collection.name not in client_coll_names:
                self.client._make_request(RequestType.CREATE_COLLECTION, json_data={
                    "name": collection.name,
                    "description": "",
                    "permission": "read_write",
                    "color": color,
                    "private": False,
                })
        self.client._refresh_client()

    def _push_parent_outline_id(self, local_path: str, created_cache: dict) -> str | None:
        """Return the Outline parent doc ID for a local file based on folder structure."""
        folder = os.path.dirname(local_path)
        readme = os.path.join(folder, 'README.md')

        # If this file IS the README, its parent is the README one level up
        if os.path.normpath(local_path) == os.path.normpath(readme):
            folder = os.path.dirname(folder)
            if os.path.normpath(folder) == os.path.normpath(self.path):
                return None  # top-level collection folder, no parent
            readme = os.path.join(folder, 'README.md')

        if readme in created_cache:
            return created_cache[readme]
        if os.path.exists(readme):
            return _read_meta(readme).get('outline_id')
        return None

    def _create_client_documents(self, local_collection: Collection) -> None:
        collection_path = os.path.join(self.path, local_collection.name)
        collection_id = next(
            (c.id for c in self.client.collections if c.name == local_collection.name), None
        )
        if not collection_id:
            return

        # Maps readme_path → newly-created Outline ID (for parent→child ordering)
        created_cache: dict[str, str] = {}

        for document in local_collection.documents:
            local_path = document.local_path or os.path.join(
                collection_path, document.name + '.md'
            )
            try:
                with open(local_path, 'r', encoding='utf-8') as f:
                    raw = f.read()
            except OSError:
                continue

            meta, body = fm_extract(raw)
            # Use separate remote_body so local file keeps _attachments/ paths
            remote_body = push_attachments(body, collection_path, self.client._upload_attachment)

            parent_id = self._push_parent_outline_id(local_path, created_cache)
            req_data: dict = {
                "title": document.name,
                "collectionId": collection_id,
                "text": remote_body,
                "publish": True,
            }
            if parent_id:
                req_data["parentDocumentId"] = parent_id

            resp = self.client._make_request(RequestType.CREATE_DOCUMENT, json_data=req_data)
            try:
                new_doc = json.loads(resp.text)['data']
                meta['outline_id'] = new_doc['id']
                meta['outline_collection_id'] = collection_id
                meta['outline_updated_at'] = new_doc['updatedAt']
                meta['outline_content_hash'] = _content_hash(body)  # LOCAL body hash
                if parent_id:
                    meta['outline_parent_id'] = parent_id

                # Write back LOCAL body (not remote_body) — keeps _attachments/ paths intact
                with open(local_path, 'w', encoding='utf-8') as f:
                    f.write(fm_inject(meta, body))

                if local_path.endswith('README.md'):
                    created_cache[local_path] = new_doc['id']
            except Exception:
                pass

        self.client._refresh_client()

    # ── Update remote ──────────────────────────────────────────────────────

    def _update_client_documents(self, collection: Collection) -> None:
        collection_path = os.path.join(self.path, collection.name)

        for document in collection.documents:
            local_path = document.local_path or os.path.join(
                collection_path, document.name + '.md'
            )
            try:
                with open(local_path, 'r', encoding='utf-8') as f:
                    raw = f.read()
            except OSError:
                continue

            meta, body = fm_extract(raw)
            remote_body = push_attachments(body, collection_path, self.client._upload_attachment)

            resp = self.client._make_request(RequestType.UPDATE_DOCUMENT, json_data={
                "id": document.id,
                "title": document.name,
                "text": remote_body,
                "append": False,
                "publish": True,
            })
            try:
                updated_doc = json.loads(resp.text)['data']
                meta['outline_updated_at'] = updated_doc['updatedAt']
                meta['outline_content_hash'] = _content_hash(body)  # LOCAL body hash

                # Write back LOCAL body — keeps _attachments/ paths intact
                with open(local_path, 'w', encoding='utf-8') as f:
                    f.write(fm_inject(meta, body))
            except Exception:
                pass

        self.client._refresh_client()

    # ── Delete helpers ─────────────────────────────────────────────────────

    def _delete_orphaned_local_docs(self) -> None:
        """Remove local files whose outline_id no longer exists in Outline."""
        client_ids = self._client_id_set()
        for collection in self.collections:
            for doc in collection.documents:
                if doc.id and doc.id not in client_ids and doc.local_path:
                    console.print(f"[bold yellow]deleting orphan: {doc.local_path}")
                    try:
                        os.remove(doc.local_path)
                        # Remove empty parent folder left behind
                        folder = os.path.dirname(doc.local_path)
                        if folder != os.path.join(self.path, collection.name):
                            try:
                                os.rmdir(folder)
                            except OSError:
                                pass
                    except OSError as e:
                        console.print(f"[bold red]could not delete {doc.local_path}: {e}")
        self._refresh_local()

    def _delete_client_collection(self, collection: Collection) -> None:
        self.client._make_request(RequestType.DELETE_COLLECTION, json_data={"id": collection.id})
        self.client._refresh_client()

    def _delete_local_collection(self, collection: Collection) -> None:
        try:
            shutil.rmtree(os.path.join(self.path, collection.name))
        except Exception as e:
            console.print(f"[bold red]Could not remove local collection {collection.name}: {e}")
        self._refresh_local()

    def _delete_client_documents(self, collection: Collection) -> None:
        for document in collection.documents:
            self.client._make_request(RequestType.DELETE_DOCUMENT, json_data={
                "id": document.id, "permanent": False,
            })
        self.client._refresh_client()

    def _delete_local_documents(self, collection: Collection) -> None:
        for document in collection.documents:
            path = document.local_path or os.path.join(
                self.path, collection.name, document.name + '.md'
            )
            try:
                os.remove(path)
            except OSError:
                pass
        self._refresh_local()

    # ── Find helper (used by delete command) ───────────────────────────────

    def _find_document(self, collection_name: str, document_name: str) -> Collection | None:
        collection = [c for c in self.client.collections if c.name == collection_name]
        if not collection:
            return None
        document = [d for d in collection[0].documents if d.name == document_name]
        return Collection(id=collection[0].id, name=collection[0].name, documents=document)

    # ── Sync ───────────────────────────────────────────────────────────────

    def sync(self, sync_type: SyncType) -> None:
        missing = self._get_missing_items(sync_type=sync_type)
        old = self._get_old_items(sync_type=sync_type)

        if sync_type == SyncType.REMOTE:
            self._create_client_collections(missing)
            console.print("Created missing collections in Outline")
            for collection in missing:
                self._create_client_documents(collection)
            console.print("Created missing documents in Outline")
            for collection in old:
                self._update_client_documents(collection)
            console.print("Updated documents in Outline")

        elif sync_type == SyncType.LOCAL:
            self._create_local_collections(missing)
            console.print("Created missing collections in local folder")
            for collection in missing:
                self._create_local_documents(collection)
            console.print("Created missing documents in local folder")
            for collection in old:
                self._update_local_documents(collection)
            console.print("Updated documents in local folder")
            self._delete_orphaned_local_docs()

    # ── Delete command ─────────────────────────────────────────────────────

    def delete(self, collection_name: str, document_name: str, all: bool) -> None:
        collection = self._find_document(collection_name=collection_name,
                                         document_name=document_name)
        if collection is None:
            console.print(f"[bold red]Unable to find {collection_name}/{document_name}")
            return

        if all:
            self._delete_client_collection(collection)
            console.print(f"[bold blue]Deleted collection '{collection_name}' from Outline")
            self._delete_local_collection(collection)
            console.print(f"[bold blue]Deleted collection '{collection_name}' from local folder")
        else:
            self._delete_client_documents(collection)
            console.print(f"[bold blue]Deleted '{collection_name}/{document_name}' from Outline")
            self._delete_local_documents(collection)
            console.print(f"[bold blue]Deleted '{collection_name}/{document_name}' from local folder")

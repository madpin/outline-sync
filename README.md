# outline-sync

Syncs your [Outline Wiki](https://www.getoutline.com/) with a local markdown folder, so AI coding assistants (Claude, Gemini, Cursor, etc.) can read your team's knowledge base directly.

## How it works

Outline is structured as **collections** containing **documents**. This tool mirrors that structure as directories and `.md` files on your local filesystem:

```
~/outline-wiki/
  Collection1/
    Document1.md
    Document2.md
  Collection2/
    Document3.md
```

Files in the root (not inside a collection directory) are ignored.

## Quick start with UV

```bash
# Clone the repo
git clone https://github.com/madpin/outline-sync
cd outline-sync

# Copy and fill in your credentials
cp example.env .env
# Edit .env with your Outline API token, base URL, and sync path

# Run a sync
uv run outline-sync sync
```

## Configuration

### `.env` file

```env
OUTLINE_API_TOKEN="ol_api_..."        # Outline API token
OUTLINE_BASE_URL="https://your-outline-instance.com"
OUTLINE_SYNC_PATH="~/outline-wiki"   # Where to store local markdown files
COLLECTION_COLOR=""                   # Optional hex color for new collections
```

`OUTLINE_SYNC_PATH` overrides the path in `conf.yml`. Set it here to avoid editing the YAML file.

### `conf.yml`

```yaml
wiki:
  path: "~/outline-sync"   # fallback if OUTLINE_SYNC_PATH is not set
  exclude:
    - private-collection   # optional: skip these collections
```

## Commands

```bash
# Pull everything from Outline into your local folder
uv run outline-sync sync

# Check what's out of sync (remote vs local)
uv run outline-sync status
uv run outline-sync status --local   # flip direction

# Delete a document or entire collection
uv run outline-sync delete -c "Collection Name" -d "Document Name"
uv run outline-sync delete -c "Collection Name" --all
```

### Options (all commands)

```
-v, --verbose    Detailed logs
-c, --config     Path to conf.yml (defaults to ./conf.yml)
-e, --env        Path to .env file (defaults to ./.env)
```

After the first run, config paths are saved to `~/.config/outline-sync/conf.json` so you don't need to pass them again.

## Development

```bash
# Install deps and run directly
uv sync
uv run outline-sync sync
```

## License

[GPL3](LICENSE.md)

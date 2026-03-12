# plover-steno-dojo

[![PyPI](https://img.shields.io/pypi/v/plover-steno-dojo)](https://pypi.org/project/plover-steno-dojo/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A [Plover](https://www.openstenoproject.org/plover/) extension that runs a local WebSocket server and broadcasts real-time stroke and machine state events to the [Steno Dojo](https://github.com/VincentCosta6/steno-dojo) app.

**No external dependencies** — uses only the Python standard library.

## Install

Search for **plover-steno-dojo** in Plover's Plug-ins Manager (`Tools → Plug-ins Manager`), then click **Install** and restart Plover.

Or from a terminal using Plover's bundled Python:

```bash
# macOS
/Applications/Plover.app/Contents/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
  -m pip install plover-steno-dojo
```

## Usage

1. After installing, open Plover's **Plug-ins Manager**
2. Find **plover-steno-dojo**, click **Enable**, then **Apply**
3. Restart Plover
4. The plugin starts automatically and listens on `ws://localhost:8086/`
5. Open Steno Dojo — it will connect automatically

## Message format

All messages are JSON objects broadcast to every connected client:

```json
{"type": "stroked", "stroke": "TEFT"}
{"type": "machine_state_changed", "machine_type": "Gemini PR", "state": "connected"}
```

## Development

```bash
git clone https://github.com/VincentCosta6/plover-steno-dojo
cd plover-steno-dojo
pip install -e .
```

## Releasing

1. Update `version = X.Y.Z` in `setup.cfg`
2. Commit and push
3. Create a GitHub Release tagged `vX.Y.Z`

The publish workflow verifies the tag matches `setup.cfg` before uploading to PyPI.

## License

MIT

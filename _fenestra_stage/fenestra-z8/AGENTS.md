# Fenestra Z8 C3.11 public source

Use the local virtual environment for Python. Follow README.md for the pinned local inputs and third-party dependencies.

Use the visible Z8 workbench for physical operations. Start it with Start-Z8-Debug.cmd and use host/workbench_client.py for programmatic access. Assign a stable unique request ID to each intended operation. After a wait or transport timeout, use the result command with the same ID. The discovery token is private and must only be read through the client. Unit tests may use explicit synthetic transports.

Keep original firmware, generated firmware and plugins, vendor tool outputs, device credentials, dumps, workbench sessions, and other runtime records local. Preserve dependency notices and keep SOURCE-INVENTORY.json and Z8-PORT-INVENTORY.json current.

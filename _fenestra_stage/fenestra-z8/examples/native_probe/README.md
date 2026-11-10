# Native profile probe

This independent Z8 C3.11 WLM uses the existing core. It declares a checked stock native function, then reads property 0x05001261 on the captured original UI task when its button is pressed. It does not contain a property setter, memory writer or hook installer.

Copy native_probe.wlm into the card's existing FENESTRA directory. Preserve all existing WLMs and STARTUP settings. On the next normal Fenestra activation, open WonderLantern → Native profile probe. This is a developer example; physical UI/getter execution has not been qualified in this delivery. No firmware update is required by this example's SDK contract.

The plugin checks the advertised stock MAIN target (without CRC), then compares the actual 16 entry bytes of 0x410d5ec8 before DECLARE/OPEN. It deliberately avoids 0x403ec398, which the feature suite replaces. A mismatch disables the read button. core.tasks/v1 must report original.ui and a nonzero captured task. If it reports no captured task, enter the normal camera menu and use Refresh native UI. Read RAW quality submits exactly one pointer-free native getter; FETCH waits for its existing ticket without submitting another getter.

The displayed result is the stored integer value, not a recording analysis or an assurance about the currently selected video mode. Source evidence maps the suite's N-RAW values 0/1/2/3 to Normal/High/Fine/Basic, but this example displays the number without extending those semantics to other firmware or properties.

Build from the standalone workspace with a new output directory:

~~~powershell
.\.venv\Scripts\python.exe host/build_sdk_module.py examples/native_probe/module.c --id native_probe --target z8-c311 --required 0x390000 --output-dir build/my-native-probe
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_native_probe.py -v
~~~

See [the Z8 SDK notes](../../docs/z8-sdk.md) for target identity and build tooling. Native profile verification is a voluntary SDK helper; raw DECLARE itself still stores original bytes as metadata without comparing them. Matching 16 bytes and an advertised target do not establish an entire native function's identity, signature or hardware safety.

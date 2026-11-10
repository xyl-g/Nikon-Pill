# Independent WLM native controls

Each feature is a separate resident EL1 image with exactly one named provider.
The WLM producer embeds that image and its own native recipes in that feature's
WLM container. No control image carries a second feature provider. The generic
firmware does not link these backends or their original function addresses.

| Component | Slot | Feature bit | Stage rank |
| --- | --- | --- | --- |
| framing | 0x40910000 | 1 | 10 |
| prores | 0x408e0000 | 2 | 11 |
| h265 | 0x408f0000 | 4 | 12 |
| log_colour | 0x40900000 | 8 | 13 |
| photo_tools | 0x40920000 | 16 | 14 |
| shared.locale | 0x40930000 | 0 | 90 |
| shared.movie | 0x408d0000 | 0 | 100 |

Every slot is 64 KiB. No allocation at or beyond 0x40951000 is changed by
this builder. The producer must independently check stock-zero IMAGE spans,
exact native preimages, dependency closure, symbols and component ownership.
Shared components are embedded by their users and deduplicated by the generic
loader; they are not a separate BOOT.FNC installation.

shared.movie contains only original MOV hook trampolines and callback dispatch.
At its final stage it resolves paired named callbacks from earlier staged
features, rejects partial pairs and validates the optional Framing original
close chain. PUBLISH only enables the already resolved state. Each native close
runs the original chain exactly once, then fans out only on successful close.
Each feature owns its own recording tracker, STOP gate and provider leases.
The Framing original close hook is a dependency of the Framing WLM, never copied
into the shared bridge. shared.locale exports only camera.locale.text and its
immutable selected-table descriptor. The producer supplies its exact table
count and directories; core/font acceptance capacity remains separate.

Build the five feature controls and one shared movie bridge:

    .venv/Scripts/python.exe nativepacks/wlm_controls/build.py --component all --out build/wlm-controls

Build one selected native locale descriptor:

    .venv/Scripts/python.exe nativepacks/wlm_controls/build.py --component locale --text-count 3225 --languages 36 --offsets-directory 0x40e00000 --pools-directory 0x40e00120 --out build/wlm-locale

The directory values above are examples; only the producer's verified selected
recipe addresses are deployable. Each output contains controls.elf and
controls.json with PT_LOAD spans, entry, unique exports, source hashes and
stage rank. The generated ELF contains no imports or runtime relocations.

Focused validation:

    .venv/Scripts/python.exe nativepacks/wlm_controls/tests/test_build.py
    .venv/Scripts/python.exe nativepacks/wlm_controls/tests/run_checks.py --out build/wlm-controls-checks

The ARM check executes the real independent C and hook assembly for all 31
nonempty subsets. Its GUI/RTOS function bodies, native heap, named registry and
remainder of original MOV close are synthetic fixtures. It is not QEMU or
physical-camera recording qualification. Published code remains resident until
reboot; module service stop/resume does not hot-unload native hooks or code.

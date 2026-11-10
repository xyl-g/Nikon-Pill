# Resident feature controls pack

This is the native EL1 backend for all five copied control services. Its control
objects, native adapter, original function profile, photo bridge, GUI poll and
shutdown policy are outside the core. The core provides only the generic entry,
verified storage, registry, original-UI dispatcher and lifecycle notifications.
No callback points into EL0 WLM memory. The existing portable controller C files
are compiled into this ELF and must be excluded from the foundation core link.

| Subset bit | Named services |
| --- | --- |
| F=1 | camera.video.framing/v1 |
| V=2 | camera.video.prores/v1, camera.video.h265/v1 |
| L=4 | camera.video.log_colour/v1 |
| P=8 | camera.photo.tools/v1 |

Build from the workspace with its .venv:

    .venv/Scripts/python.exe nativepacks/feature_controls/build.py --features 15 --out build/controls-pack

The fixed ET_EXEC image is at 0x408d0000 and must end before 0x40950000. The
composer independently checks original free space and every selected native
recipe. 0x408c0000 is unsuitable because framing uses that address. Separate RX
and RW/BSS PT_LOAD segments are emitted with no dynamic imports, TLS or runtime
relocations. Every nonempty F/V/L/P subset is supported; absent provider families
are removed by compile-time dispatch plus section GC. Shared MOV tracking remains
in every subset because framing also needs a recording guard.

controls.json contains the ELF identity, exact feature marker, entry, segments,
symbols, compiler inputs and native dependencies. The pinned compiled.json digest
and a named dependency table/count are also embedded in the ELF for independent
composer verification. Dependencies are private pack profile data, not core
feature names. Only enabled groups export their corresponding upstream native
functions. Original stock functions are not exported as pack-owned code.

The generic ABI is include/fenestra_native_pack.h. Staging entry performs one
2136-byte, 16-byte-aligned host allocation (confirmed by actual ARM allocator tests) and initializes copied descriptors;
it invokes no original function or original allocator. The host may use its
preallocated staging pool while other CPUs are parked. The target
PUBLISH transition is no-allocation/no-failure and completes before remote CPU
release. It is logically atomic to excluded native consumers, not one hardware
atomic instruction. All providers declare ORIGINAL_UI for copied CALL;
ATTACH and DETACH manipulate their own lease/state plus checked bank reads and do
not invoke F_GET. The first framing QUERY performs the exact native getter.

Native MOV gateways preserve the reviewed register/displaced-instruction paths.
A framing subset forwards successful-close processing through the upstream Open
Gate close function; other subsets replay the original close prologue. Settings
and photo START are blocked while a MOV is observed. Uncertain/duplicate MOV
ownership fails closed and retains the pack. A setting rejected for recording
is not automatically applied after the movie ends.

Photo START only queues. The original GUI poll claims a fresh serial and starts
or reuses the resident upstream worker; encoding and file IO stay in that worker.
STOP closes admission and writes cancellation to the matching native job even
without further GUI execution. GUI polls continue during stop to restore owned
timer/menu state and observe completion. Five-second unclaimed requests revoke
the serial before a late worker can claim it. RESUME retains serials and requires
no active job, fault, uncertain movie or old provider owner lease.

Published images, contexts and permanent worker code remain resident until the
runtime ends. STOP acknowledgement permits logical quiescence; it is not an
unload acknowledgement for the permanent worker or late native callback routes.
There is no hot unmap/overwrite path. Removing/replacing card files stages a
future safe activation, not destruction of resident code.

The five EL0 frontends retain their v1 service payloads. BUSY with
reply.reserved=FX_SERVICE_DEFERRED means retry the same logical request with a new
transport sequence; actual provider BUSY retains its original reserved value.
This keeps queued SELECT/START/CANCEL requests retrievable without delaying a
recording-rejected setting. Updated frontends must accompany this registry.

Run tests/run_checks.py --out build/controls-checks for all 15 strict native
builds and actual AArch64 entry/attach/query/stop/detach/resume under Unicorn,
plus host tests of all five protocols, owner fencing, stop reentrancy, worker
reuse, cancellation, late claim and fault retention. Original native functions
are synthetic stubs in these tests. Workspace unit tests additionally exercise
the real generic registry and all five frontend state machines with deferred
replies. These checks do not qualify real RTOS tasks, GUI servicing latency,
SMP/cache/DMA publication, media IO or physical recording.


P=8 also owns late-load DCF subscription compensation. The original constructor
has already copied five event IDs into a 48-slot CPU dispatcher bitmap; changing
that constructor's future instructions does not update the existing slot. Stage
checks exact original constructor/registration code windows, then requires one
unique callback 0x408703b4, type 0x04320000, zero native header fields and a complete
old-five or exact new-nine bitmap. Any duplicate, partial or unknown state rejects
entry before descriptors are published. Stage and ABORT do not write the native
table or invoke a native mutex, allocator or constructor. The host staging pool
is preallocated before native exclusion; this pack needs only 2136 aligned bytes,
well within the 128 KiB pool.

For this C311 host, exclusion leave performs a synchronization epoch and waits for
all three CPUs' ACKs. It does not release the remote callbacks. The synchronous
capsule work callback subsequently invokes registry/pack PUBLISH, and only after
that work returns does native_callback set exit_requested and release the remote
CPUs. Stage through PUBLISH therefore share one stopped-CPU epoch (parked=7,
held=1, exit_requested=0). An alternate host must preserve this contract before
using this target pack; no original mutex can be acquired while its owner might
be parked. The table is consumed directly by the CPU dispatcher, with no extra
subscription bitmap cache in the reviewed call path.

PUBLISH preserves the callback, existing handle and client callback. For old-five
state, the event-to-bitmap algorithm stores two bitmap bytes and one aligned
32-bit count. This is six bytes stored and three bytes whose values change:
slot-relative +0x18 (5->9), +0xdc (3->0x73), +0xdd (0->1). Count/bitmap cache
lines are cleaned with CTR_EL0 DminLine-derived DC CVAC to PoC followed by DSB SY,
then consumers are release-published. An exact new-nine state is a write-free
idempotent case; repeated PUBLISH, STOP and RESUME do not register again or change
that permanent slot. The real record begins at base+4+slot*0xf98, with its bitmap
at base+slot*0xf98+0x1c for 0xf80 bytes; the leading four bytes are a global prefix.

The standalone check_precapture_arm.py test executes actual original registration
and early-patched constructors at slots 0/7/47 and compares their complete records
to late pack publication. It checks cache operations, exact stores, rejection and
ABORT paths, plus the read-only non-atomic physical slot-12 snapshot. The separate
full adapter fixture verifies PUBLISH-before-remote-release ordering and actual
original event dispatch. These are offline checks, not new camera execution.

The card pack exports immutable `camera.locale.text` ABI 1 data (32 bytes) via
`fx_controls_locale_export`. Its text-table length is derived from the pinned
selected recipe, including the 36-language table stride. This is an offset-table
bound, not the native accepted-ID limit. The core owns that separate limit and
publishes it with the font-bank capacity. The pack never writes the shared
native limit. The actual ARM entry checks verify the copied DATA registration
and the immutable descriptor bytes for every subset.

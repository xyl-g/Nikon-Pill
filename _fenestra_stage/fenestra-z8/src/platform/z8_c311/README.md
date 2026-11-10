# Z8 C3.11 minimal diagnostic port

This project-owned adaptation reuses Fenestra's shared PTP transport, native dispatcher adapter, DeviceInfo array helper and freestanding memory routines. The upstream LICENSE and EXCEPTIONS remain applicable. No Nikon code bytes, decoded tables or firmware are included in these source files.

The endpoint implements only status, echo and application/source-build identity (capabilities 259). It does not load modules and has no RTOS monitor, Linux bridge, native-call or memory-access backend. Job control retains the shared bounded queue, cancellation and disconnect behavior. Backends execute on a separate native task, never in a USB callback.

## Local input identity

Use the extracted MAIN component **without** the component's trailing two-byte CRC:

- 81,807,709 bytes
- SHA-256: 2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853

The 81,807,711-byte EG component is a different input. Neither input is a complete update package.

## Bindings

The exact required field layout is in bindings.example.json. Every null is unknown and rejected. Addresses accept integer values or hexadecimal strings. No Z9 address is a fallback. Fields mean:

- application: MAIN image base, length and pinned SHA-256.
- code: separately established empty executable reservation; page aligned, after the original boot prefix.
- ram: native heap/data address bounds.
- native: allocator, task create/resume/delay and PTP payload/capacity/incoming/response/send entry addresses. Allocation has the verified (u32 bytes, u32 pool) -> pointer ABI; allocate_pool is its second argument.
- data: seven page-root pointers, current PTP session, original OperationsSupported array and its readable capacity.
- anchor: stock page group/ID, original descriptor address and exact initialized 64 descriptor bytes. The verified native page ABI is 96 bytes with descriptor at +80. The heap owner begins with an unchanged clone of those 64 bytes.
- events: system-message ID offset, startup-complete and normal-shutdown IDs, independently found in Z8.
- task: original task attributes/priority/processor/affinity, owned aligned stack size and native delay ticks. The task record ABI is 56 bytes: context +8, entry +16, priority +24, stack bytes +32, stack +40, processor +48, affinity +52. The native creator must copy the temporary record before returning.
- hooks: startup dispatcher and three PTP entry hooks. Each has an address, exact original 16-byte window and address+16 continuation. Only straight-line, non-PC-relative windows are supported. Separate validation must establish x16 is disposable at every continuation.

No raw BSS slot is presumed unused. On entry to startup-complete, preparation validates the already initialized page/descriptor, allocates an owner, clones the unchanged 64 descriptor bytes, initializes a disabled PTP queue and publishes PREPARED with an atomic compare/exchange. It creates no task at this stage. Stock startup is then forwarded exactly once. After its return, the hook verifies that the same owner is still anchored, atomically changes PREPARED to STARTING, checks shutdown, and allocates/creates/resumes the worker. Only the actual running worker can change STARTING to ACTIVE and enable the endpoint. A shutdown can close the published owner while stock startup runs; a later startup return or worker entry cannot reopen CLOSED.

Native page roots, the stock descriptor and the allocator must be valid before this startup event enters the hook. If preparation fails, this startup attempt stays closed: there is no late installation fallback after stock returns. With no owner, no owner error code can be retained; native PTP handlers report a closed endpoint. Also verify ordering from hook entry through first publication against normal shutdown. Prepublication remains an ownerless interval; this design does not invent an unknown shutdown latch. Reentrancy/serialization of startup events remains a required native validation item.

The phases are PREPARED=1, STARTING=2, ACTIVE=3, CLOSED=4 and FAILED=5. Shutdown publishes CLOSED before forwarding stock shutdown exactly once. Failure publishes FAILED and an error code unless shutdown has already closed the owner. Published storage, descriptor and task stack remain retained until reboot, including after task creation/resume failures or a shutdown while creation is in progress. A created but unresumed task is retained, never deleted through an unverified API. Disconnect invalidates the shared protocol generation. There is no hot unload. The metadata symbol exposes state/phase offsets for independent instruction tests. Status words are firmware, active, capabilities, native/error code, phase and worker-running, followed by two reserved zero words.

The descriptor clone requires evidence that stock consumers accept an equivalent descriptor at a new heap address, do not replace its owner pointer during this lifetime, and treat the copied first 64 bytes according to the verified ABI. This is not established by changing a firmware hash.

## Builds and independent validation

From the project root, a complete binding profile can produce only ELF/bin plus a provenance report:

    python -B host/build_z8_diag.py --bindings build/z8-port/bindings.json --output-dir build/z8-diag-elf

The builder never runs camera firmware. Application-image emission additionally requires the exact original MAIN and a separate validation report:

    python -B host/build_z8_diag.py --bindings build/z8-port/bindings.json --firmware inputs/z8-c311-main.bin --validation build/z8-port/native-validation.json --output-dir build/z8-diag-candidate

That report must have schema fenestra.z8-diag-validation/v1, target z8-c311, passed true, bindings_sha256 matching the complete bindings file's raw bytes, application_sha256 matching MAIN, and a checks object whose task_abi, ptp_abi, events, anchor, native_bindings, hook_windows, executable_space and scratch_x16 entries are all true. The report producer must supply actual independent evidence for those conclusions. The builder never synthesizes a passing report.

Every image build also checks original hook bytes, the zero-filled reservation, linked displaced windows and continuation literals, direct branches to actual ELF symbols, code bounds and complete byte-for-byte original reconstruction after declared patches are reversed. All compiler dependencies enter the build identity. Writable static .data/.bss sections are forbidden; runtime state belongs to the heap.

All outputs remain experimental, unqualified for physical hardware, and flashable=false. The generated .main is an application analysis candidate, not a repacked updater. Neither a compiler pass nor a static native validation report proves startup, USB, task scheduling, cache behavior, power-off or physical-camera operation.


## Instruction tests

After building build/z8-diag-compile-v2, run:

    python -B tests/test_z8_diag_emulation.py

Set Z8_DIAG_BUILD to select another local build directory. The tests read ELF symbols and the compiled metadata array for owner layouts; they execute the compiled diagnostic A64 core in Unicorn. Native allocation, task creation/resume/delay, original dispatcher and PTP transfer functions are explicit boundary fixtures. Tests cover clone/publication, startup/shutdown ordering and failures, unchanged stock page bytes, deferred enabling and the bounded FXP2 status/echo/identity path. The emulation.json result records the tested code hash and the unmodeled hardware scope. No original Nikon function body, full-system boot or physical USB device is executed.

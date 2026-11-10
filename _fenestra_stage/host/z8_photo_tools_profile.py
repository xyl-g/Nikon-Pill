"""Pinned ABI facts for the reviewed upstream photo-tools backend; no USB access."""
UPSTREAM_COMMIT = "a21fc8c04b6dd863439c4192d387b755633287f6"
RUNTIME_ADDRESS = 0xA0FFED000
RUNTIME_RESERVED = 12288
RUNTIME_BYTES = 10496
RUNTIME_MAGIC = 0x42534D50
TASK_REGISTRY = 0xA03203E08
RUNTIME_OFFSETS = {
    "magic":0,"state":4,"cancel":8,"status":12,"serial":16,"claimed":20,"modal":24,
    "worker":48,"worker_id":56,"workers":60,"spawned":64,"context.rows":396,
    "context.total":400,"context.phase":404,"context.phase_first":408,"context.phase_second":412,
    "context.serial":416,"job_mode":480,"group_index":484,"group_count":488,"merged":492,
    "targets":496,"attempted":500,"seconds":504,"browser_open":1696,"operation":1700,
    "modal_serial":10464,"result":10468,"review_serial":10484,
}
SYMBOLS = {"psm_worker":0x409A44CC,"psm_camera_job":0x40995818}

def validate_metadata(metadata):
    symbols = metadata["units"]["pixelshift"]["symbols"]
    for name, expected in SYMBOLS.items():
        if int(symbols[name]["va"],16) != expected:
            raise ValueError("photo-tools reviewed symbol mismatch: " + name)
    return dict(runtime=hex(RUNTIME_ADDRESS), runtime_bytes=RUNTIME_BYTES,
                permanent_worker=hex(SYMBOLS["psm_worker"]), camera_job=hex(SYMBOLS["psm_camera_job"]))

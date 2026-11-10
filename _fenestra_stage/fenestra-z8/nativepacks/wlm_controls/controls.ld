ENTRY(fx_native_pack_entry)
PHDRS { code PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
  . = fx_wlm_base;
  .text : ALIGN(16) { *(.text*) } :code
  .rodata : ALIGN(16) { *(.rodata*) } :code
  . = ALIGN(4096);
  .data : ALIGN(16) { *(.data*) } :data
  .bss (NOLOAD) : ALIGN(16) { *(.bss*) *(COMMON) } :data
  __fx_wlm_end = ALIGN(4096);
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.ARM.exidx*) }
  PROVIDE(fx_wlm_open_resume = 0x40cebe40);
  PROVIDE(fx_wlm_close_resume = 0x40ceef14);
  ASSERT(__fx_wlm_end <= fx_wlm_base + 65536, "component exceeds its independent 64KB slot")
}

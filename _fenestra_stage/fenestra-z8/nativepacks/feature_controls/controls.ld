ENTRY(fx_native_pack_entry)
fx_native_pack_entry = fx_controls_pack_entry;
PHDRS { code PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
  . = 0x408d0000;
  .text : ALIGN(16) { *(.text*) } :code
  .rodata : ALIGN(16) { *(.rodata*) } :code
  . = ALIGN(4096);
  .data : ALIGN(16) { *(.data*) } :data
  .bss (NOLOAD) : ALIGN(16) { *(.bss*) *(COMMON) } :data
  __fx_controls_end = ALIGN(4096);
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.ARM.exidx*) }
  PROVIDE(fx_controls_open_resume = 0x40cebe40);
  PROVIDE(fx_controls_close_resume = 0x40ceef14);
  ASSERT(__fx_controls_end <= 0x40950000, "controls pack exceeds verified reserved window")
}

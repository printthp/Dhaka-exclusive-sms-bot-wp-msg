; engine.asm - x86_64 Linux NASM (PIC-safe)
; Assemble: nasm -f elf64 engine.asm -o engine_asm.o
; Link:     gcc -shared -fPIC engine_asm.o -o asm_engine.so

default rel

section .rodata
    status_msg:    db "ASM_ENGINE_ACTIVE", 0
    catalog_msg:   db "ASM_CATALOG_SYNCED", 0
    train_msg:     db "ASM_AI_TRAINING_INIT", 0
    help_msg:      db "ASM commands: asm_status, asm_catalog, asm_train, asm_help", 0
    unknown_msg:   db "ASM_COMMAND_UNKNOWN", 0

section .text
    global asm_process_command
    global asm_strlen
    global asm_checksum

; ============================================================
; asm_process_command(const char* cmd) -> const char*
; Dispatches commands starting with "asm_"
; ============================================================
asm_process_command:
    push rbp
    mov  rbp, rsp

    ; Check if command starts with "asm_" (4 bytes)
    mov  eax, [rdi]
    cmp  eax, 0x5F6D7361          ; "asm_" little-endian
    jne  .unknown_cmd

    ; Check the 5th character to dispatch
    movzx eax, byte [rdi + 4]

    cmp  al, 's'
    je   .status_cmd        ; asm_status
    cmp  al, 'c'
    je   .catalog_cmd       ; asm_catalog
    cmp  al, 't'
    je   .train_cmd         ; asm_train
    cmp  al, 'h'
    je   .help_cmd          ; asm_help

.unknown_cmd:
    lea  rax, [unknown_msg]
    jmp  .done

.status_cmd:
    lea  rax, [status_msg]
    jmp  .done

.catalog_cmd:
    lea  rax, [catalog_msg]
    jmp  .done

.train_cmd:
    lea  rax, [train_msg]
    jmp  .done

.help_cmd:
    lea  rax, [help_msg]

.done:
    pop  rbp
    ret

; ============================================================
; asm_strlen(const char* str) -> uint64
; Pure assembly string length
; ============================================================
asm_strlen:
    xor  rax, rax
.loop:
    cmp  byte [rdi + rax], 0
    je   .done
    inc  rax
    jmp  .loop
.done:
    ret

; ============================================================
; asm_checksum(const char* str) -> uint64
; XOR-based rolling checksum in assembly
; ============================================================
asm_checksum:
    xor  rax, rax
.loop:
    movzx ecx, byte [rdi]
    test  cl, cl
    jz   .done
    xor  rax, rcx
    shl  rax, 1
    add  rdi, 1
    jmp  .loop
.done:
    ret

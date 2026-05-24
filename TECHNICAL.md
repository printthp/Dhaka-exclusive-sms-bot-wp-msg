# Deep Technical Dive — BIZ-CORE MASTER SYSTEM

## 1. Architecture Layers

### Layer 1: Python (Orchestration)
- **Flask** handles HTTP, routing, JSON, auth
- **SQLite** stores persistent data with `threading.Lock` for thread safety
- **ctypes** loads `.so` files and marshals data between Python and native code

### Layer 2: C++ (Business Logic)
- Compiled with `g++ -shared -fPIC`
- Exports `process_business_logic(const char*) → const char*`
- String comparison using `std::string`

### Layer 3: x86_64 Assembly (Low-Level Primitives)
- Written in NASM syntax
- Position-Independent Code (PIC) using `default rel` and `lea`
- Exports `asm_process_command`, `asm_strlen`, `asm_checksum`
- Compiled with `nasm -f elf64` then linked with `gcc -shared`

---

## 2. How Python Calls Assembly

```python
import ctypes

# Load the shared object
asm_lib = ctypes.CDLL("./asm_engine.so")

# Tell ctypes the return type (default is int)
asm_lib.asm_process_command.restype = ctypes.c_char_p
asm_lib.asm_strlen.restype = ctypes.c_uint64
asm_lib.asm_checksum.restype = ctypes.c_uint64

# Call it — Python bytes → C char* → ASM → Python bytes
result = asm_lib.asm_process_command(b"asm_status")
print(result.decode())   # "ASM_ENGINE_ACTIVE"
```

What happens under the hood:
1. `CDLL` uses `dlopen()` to load `asm_engine.so`
2. `ctypes` resolves `asm_process_command` via `dlsym()`
3. `b"asm_status"` creates a null-terminated byte array in Python
4. `ctypes` passes the pointer to `rdi` (System V AMD64 ABI)
5. Assembly reads `rdi`, processes, returns pointer in `rax`
6. `ctypes` reads the C string from `rax` and wraps it as `bytes`

---

## 3. Assembly Engine — Line-by-Line

### Header
```nasm
default rel
```
- **Purpose**: All label references are RIP-relative by default
- **Why**: Required for Position-Independent Code (PIC). In a shared library, absolute addresses change every time the library is loaded. RIP-relative addressing works from any base address.

### Data Section
```nasm
section .rodata
    status_msg:    db "ASM_ENGINE_ACTIVE", 0
```
- `section .rodata`: Read-only data. The linker marks this page non-writable, improving security.
- `db "...", 0`: Define bytes with null terminator. C strings must end in `0x00`.

### `asm_process_command` (Command Dispatcher)
```nasm
asm_process_command:
    push rbp
    mov  rbp, rsp
```
- Standard function prologue. Saves old base pointer, sets up stack frame.
- `rbp` is used for stack unwinding in debuggers. Even though this function doesn't use the stack, it keeps frame info for stack traces.

```nasm
    mov  eax, [rdi]
    cmp  eax, 0x5F6D7361
    jne  .unknown_cmd
```
- `rdi` holds the first argument (command string pointer) per the System V AMD64 ABI.
- `mov eax, [rdi]` reads the first 4 bytes of the string into `eax`.
- `0x5F6D7361` is `"asm_"` in little-endian byte order:
  - `0x61 = 'a'`, `0x73 = 's'`, `0x6D = 'm'`, `0x5F = '_'`
- If the command doesn't start with `asm_`, jump to unknown handler.

```nasm
    movzx eax, byte [rdi + 4]
    cmp  al, 's'
    je   .status_cmd
```
- `movzx` (Move with Zero-Extend) loads the 5th character into `eax`, zeroing the upper bits.
- Compare with `'s'` for `asm_status`, `'c'` for `asm_catalog`, etc.

```nasm
.status_cmd:
    lea  rax, [status_msg]
    jmp  .done
```
- `lea` (Load Effective Address) puts the RIP-relative address of `status_msg` into `rax`.
- We use `lea` instead of `mov rax, status_msg` because `default rel` makes `lea` RIP-relative and PIC-safe.
- `rax` holds the return value per AMD64 calling convention.

```nasm
    pop  rbp
    ret
```
- Restore old base pointer and return to caller.

### `asm_strlen` (Pure Assembly String Length)
```nasm
asm_strlen:
    xor  rax, rax       ; counter = 0
.loop:
    cmp  byte [rdi + rax], 0
    je   .done          ; found null terminator
    inc  rax            ; counter++
    jmp  .loop
.done:
    ret
```
- Returns `uint64` length in `rax`.
- Same algorithm as `strlen(3)` in libc, but implemented in raw x86_64.

### `asm_checksum` (XOR Rolling Hash)
```nasm
asm_checksum:
    xor  rax, rax       ; hash = 0
.loop:
    movzx ecx, byte [rdi]
    test  cl, cl        ; check null terminator
    jz   .done
    xor  rax, rcx       ; hash ^= char
    shl  rax, 1         ; hash <<= 1
    add  rdi, 1         ; pointer++
    jmp  .loop
.done:
    ret
```
- Algorithm: `hash = ((hash ^ char) << 1)` for each byte.
- `test cl, cl` sets the zero flag if `cl == 0`, faster than `cmp`.
- Used for quick data integrity checks or simple hashing.

---

## 4. Build Pipeline Explained

### C++ Engine
```bash
g++ -shared -o engine.so -fPIC engine.cpp
```
- `-shared`: Produce a shared library instead of an executable.
- `-fPIC`: Generate Position-Independent Code. Required for shared objects on modern Linux.
- `extern "C"` in the C++ code prevents C++ name mangling, so the symbol is exported as `process_business_logic` instead of `_Z...`.

### Assembly Engine
```bash
nasm -f elf64 engine.asm -o engine_asm.o
```
- `-f elf64`: Output 64-bit ELF object file.
- Creates relocatable object code with symbol table.

```bash
gcc -shared -fPIC engine_asm.o -o asm_engine.so
```
- Links the NASM object into a shared library.
- `gcc` acts as the linker driver, adding necessary startup code and resolving relocations.

### Why PIC Matters
Without `-fPIC` / `default rel`:
- The linker creates `DT_TEXTREL` (text relocations).
- The dynamic loader must modify code pages at runtime, which breaks W^X (Write XOR Execute) security.
- SELinux and hardened kernels may refuse to load the library.

With proper PIC:
- All addresses are RIP-relative (`[rip + ...]`).
- The `.text` section remains read-only and executable.
- The library can be loaded at any virtual address (ASLR-friendly).

---

## 5. Database Design

### Thread Safety
```python
db_lock = Lock()

def db_query(...):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        ...
```
- SQLite supports multi-threading only if each thread uses its own connection.
- The `Lock` ensures only one thread opens/closes the DB at a time, preventing `database is locked` errors under Gunicorn's worker processes.

### Schema
- **messages**: WhatsApp/SMS inbound/outbound message log
- **sessions**: Per-phone conversation state machine context
- **orders**: E-commerce orders with Pathao delivery integration
- **products**: Facebook catalog synced products
- **users**: Customer profiles
- **settings**: Key-value config (tokens, API keys, delivery fees)
- **agents**: Human support reps with roles
- **agent_logs**: Audit trail
- **complaints**: Customer complaint tracking

---

## 6. Authentication Flow

```
Client Request
    ├─── Header: X-Admin-Token: admin123
    └───→ Flask require_token()
              └─── Compare with ADMIN_TOKEN env var
                    └─── Match? → Proceed to API
                    └─── Fail? → 401 Unauthorized
```

**Token is checked on:**
- `/api/execute`
- `/api/asm/execute`
- `/api/settings`

**Not required on:**
- `/` (home)
- `/health` (monitoring)
- `/api/asm/strlen` (utility — can be restricted if needed)
- `/api/asm/checksum` (utility)

---

## 7. Flask → Gunicorn Bridge

```python
app = Flask(__name__)
application = app  # Gunicorn looks for this variable
```

Gunicorn's `import_app("app:application")` does:
1. `import app` (the Python module)
2. `getattr(app, "application")` → returns the Flask app object
3. Wraps it in a WSGI callable and starts the HTTP server

---

## 8. Render-Specific Deployment

### Why `PORT` Matters
Render assigns a random port per deployment. You must bind to it:
```bash
gunicorn --bind 0.0.0.0:$PORT app:application
```

If you hardcode port 5000:
- Render will scan and report "No open ports detected"
- The service will fail health checks and be marked offline

### Build vs Start
| Phase | Render runs | Your project |
|-------|-------------|------------|
| Build | `make && pip install -r requirements.txt` | Compiles `.so` files, installs Flask/Gunicorn |
| Start | `gunicorn --bind 0.0.0.0:$PORT app:application` | Serves HTTP requests |

The `.so` files compiled during Build persist in the runtime filesystem.

---

## 9. Extending the Assembly Engine

### Adding a New Command
1. **Add message in `.rodata`**:
```nasm
    refund_msg:    db "ASM_REFUND_PROCESSED", 0
```

2. **Add dispatch in `.text`**:
```nasm
    cmp  al, 'r'
    je   .refund_cmd

.refund_cmd:
    lea  rax, [refund_msg]
    jmp  .done
```

3. **Rebuild**:
```bash
make clean && make
```

4. **Test**:
```bash
curl -X POST ... -d '{"cmd":"asm_refund"}'
```

### Adding a New Algorithm
Example: `asm_reverse(const char* str) → char*`
- Allocate a buffer (caller must free, or use a static thread-local buffer)
- Walk the string backwards
- Return the reversed copy

Caveat: Assembly functions returning newly allocated memory require the caller to `free()`. Since `ctypes` doesn't automatically manage C memory, use static buffers or add a companion `asm_free()` export.

---

## 10. Docker Setup (Optional)

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y g++ gcc nasm make

WORKDIR /app
COPY . .

RUN make && pip install -r requirements.txt

ENV SECRET_KEY=change-me
ENV ADMIN_TOKEN=change-me

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:application"]
```

```bash
docker build -t bizcore .
docker run -p 5000:5000 bizcore
```

---

## 11. Performance Notes

| Component | Bottleneck | Tip |
|-----------|-----------|-----|
| SQLite | Single-threaded writes | Use WAL mode (`PRAGMA journal_mode=WAL`) for better concurrency |
| Assembly | Zero overhead | Already optimal. No Python or C++ abstraction penalty. |
| C++ | `std::string` comparison | For 1000+ commands, use a `std::unordered_map` instead of if-chain |
| Gunicorn | Worker count | Set `--workers 2` or `--workers 4` based on Render plan CPU cores |

Enable WAL mode in `init_db()`:
```python
c.execute("PRAGMA journal_mode=WAL")
```

---

## 12. Troubleshooting

### `AppImportError: Failed to find attribute 'application'`
- The Python module name before `:` doesn't match the filename.
- If your file is `SMS_BOT_1.py`, use `SMS_BOT_1:application`.
- This project uses `app.py` → `app:application`.

### `No open ports detected`
- Gunicorn crashed before binding.
- Check that `application = app` exists in the module.
- Verify `--bind 0.0.0.0:$PORT` is used.

### `Engine Not Found`
- The `.so` file wasn't compiled or isn't in the working directory.
- Run `make` and ensure `engine.so` and `asm_engine.so` exist.
- Render: verify `make` is in the Build Command.

### `401 Unauthorized`
- Header is missing or incorrect.
- Default token: `admin123` (change in production).
- Header name: `X-Admin-Token`.

### Segfault from Assembly
- Likely a null pointer passed to `asm_strlen` or `asm_checksum`.
- Ensure Python never passes `None` or empty bytes without null terminator.
- `ctypes` automatically null-terminates `bytes` strings.

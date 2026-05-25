all:
	g++ -shared -o engine.so -fPIC engine.cpp
	nasm -f elf64 engine.asm -o engine_asm.o
	gcc -shared -fPIC engine_asm.o -o asm_engine.so
	@echo "Build complete: engine.so + asm_engine.so"

clean:
	rm -f engine.so asm_engine.so engine_asm.o

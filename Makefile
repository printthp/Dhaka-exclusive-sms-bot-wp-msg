all:
	g++ -shared -o engine.so -fPIC engine.cpp
	@echo "Build complete: engine.so"

clean:
	rm -f engine.so

set -e; cd "$(dirname "$0")"; mkdir -p build
mojo build -O3 bench.mojo -o build/bench
./build/bench

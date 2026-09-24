set -e; cd "$(dirname "$0")"; mkdir -p build
mojo build -O3 --emit shared-lib zc.mojo -o build/zc.so
python bench.py

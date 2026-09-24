set -e; cd "$(dirname "$0")"; mkdir -p build
time mojo build -O3 --emit shared-lib mojo_ext.mojo -o build/mojo_ext.so
python bench.py

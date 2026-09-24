set -e; cd "$(dirname "$0")"; mkdir -p build
mojo build -O3 --emit shared-lib cell7.mojo -o build/cell7.so
python bench.py
python compile_cost.py

set -e; cd "$(dirname "$0")"
python bench.py
python reload_check.py
python cabi_check.py
printf "var x = 40\nprint(x + 2)\n" | mojo repl

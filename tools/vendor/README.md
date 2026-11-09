# Terminal dependencies

These unmodified pure-Python wheels are loaded locally with zipimport; no runtime
network or pip installation is required. Hashes are verified by
`workbench_terminal.py`. Wheels include library source and license notices.

- pyte 0.8.2 (LGPL-3.0): https://pypi.org/project/pyte/0.8.2/
- wcwidth 0.2.13 (MIT): https://pypi.org/project/wcwidth/0.2.13/

The application renderer is separate from these libraries. Users can replace
the libraries and adjust the integrity pins in the installed Python source.

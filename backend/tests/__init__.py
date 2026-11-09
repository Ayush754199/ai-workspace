"""Test package marker.

Without this file the test modules are only importable when the current working
directory happens to be ``backend/``. As a package they resolve the same way from
the repository root, from ``backend/``, and under any runner (pytest, IDE, CI).
"""
